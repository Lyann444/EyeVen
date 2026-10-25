import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator
from app.services.glm_service import GlmApiError, GlmMalformedOutputError


class WorkflowOrchestrationTests(unittest.TestCase):
    def test_optional_business_type_other_does_not_trigger_agentic_recovery(self):
        snapshot = {
            "fields": [{
                "field_name": "business_type_other",
                "status": "missing",
                "follow_up_eligible": False,
            }],
            "policy": None,
            "verification": None,
        }

        self.assertFalse(WorkflowOrchestrator._has_unresolved_issues(snapshot))

    def test_missing_follow_up_eligible_field_triggers_agentic_recovery(self):
        snapshot = {
            "fields": [{
                "field_name": "bank_account_number",
                "status": "missing",
                "follow_up_eligible": True,
            }],
            "policy": None,
            "verification": None,
        }

        self.assertTrue(WorkflowOrchestrator._has_unresolved_issues(snapshot))

    def test_deterministic_follow_up_requires_reviewed_missing_evidence(self):
        snapshot = {
            "follow_up_candidates": [{
                "field_name": "bank_account_number",
                "availability": "not_found_in_reviewed_documents",
            }],
            "case_evidence_assessment": {
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
        }

        plan = WorkflowOrchestrator._deterministic_recovery_plan(snapshot)

        self.assertEqual(plan["action"], "request_information")
        self.assertIn("reviewing the submitted documents", plan["reason"])

    def test_deterministic_follow_up_rejects_unreviewed_or_ambiguous_evidence(self):
        for availability, ambiguous_fields, status in [
            ("documents_not_yet_reviewed", [], "missing"),
            ("not_found_in_reviewed_documents", ["bank_account_number"], "missing"),
            ("not_found_in_reviewed_documents", [], "low_confidence"),
        ]:
            snapshot = {
                "follow_up_candidates": [{
                    "field_name": "bank_account_number",
                    "availability": availability,
                    "status": status,
                }],
                "case_evidence_assessment": {
                    "ambiguous_fields": ambiguous_fields,
                    "documents_added_after_last_structuring": False,
                },
            }
            self.assertIsNone(
                WorkflowOrchestrator._deterministic_recovery_plan(snapshot)
            )

    def test_agentic_planning_is_reserved_for_conflicts_and_cross_stage_risk(self):
        self.assertTrue(WorkflowOrchestrator._requires_agentic_planning({
            "case_evidence_assessment": {"ambiguous_fields": ["company_registered_number"]},
        }))
        self.assertTrue(WorkflowOrchestrator._requires_agentic_planning({
            "case_evidence_assessment": {},
            "verification": {"mismatch_severity": "critical"},
        }))
        self.assertFalse(WorkflowOrchestrator._requires_agentic_planning({
            "case_evidence_assessment": {},
            "verification": {"mismatch_severity": "minor"},
            "policy": {"risk_band": "low", "blocking_issue_count": 0},
            "fields": [],
        }))

    def test_automatic_retry_only_runs_for_explicitly_retryable_failures(self):
        with patch(
            "app.orchestrators.reprocessing_orchestrator.ReprocessingOrchestrator.reprocess",
            return_value={"status": "failed", "reprocessing_run_id": 5},
        ) as reprocess:
            attempts, failed_actions = (
                WorkflowOrchestrator._retry_retryable_stage_failures(
                    123,
                    {
                        "verification": {
                            "status": "glm_failed",
                            "retryable": True,
                        },
                        "policy_evaluation": {
                            "status": "glm_failed",
                            "retryable": False,
                        },
                    },
                    [],
                )
            )

        reprocess.assert_called_once_with(123, "verification", "automatic_retry")
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(failed_actions[0]["target_stage"], "verification")

    def test_retryable_glm_errors_are_limited_to_transient_api_failures(self):
        self.assertTrue(
            WorkflowOrchestrator._is_retryable_glm_error(
                GlmApiError("GLM API timeout")
            )
        )
        self.assertTrue(
            WorkflowOrchestrator._is_retryable_glm_error(
                GlmApiError("GLM API HTTP error: 503 Service Unavailable")
            )
        )
        self.assertFalse(
            WorkflowOrchestrator._is_retryable_glm_error(
                GlmApiError("GLM API HTTP error: 401 Unauthorized")
            )
        )
        self.assertFalse(
            WorkflowOrchestrator._is_retryable_glm_error(
                GlmMalformedOutputError("invalid JSON")
            )
        )

    def test_unmatched_registry_result_with_conflicting_evidence_forces_review(self):
        plan = WorkflowOrchestrator._registry_recovery_plan(
            [{
                "action": "targeted_verification",
                "result": {
                    "match_status": "no_match",
                    "unresolved_fields": ["ssm_roc_rob_number"],
                },
            }],
            {
                "follow_up_candidates": [
                    {
                        "field_name": "ssm_roc_rob_number",
                        "status": "unverifiable",
                        "availability": "ambiguous_or_conflicting",
                    }
                ],
                "follow_up_draft_count": 0,
            },
        )

        self.assertEqual(plan["action"], "manual_review")
        self.assertEqual(plan["target_fields"], [])
        self.assertIsNone(plan["target_stage"])

    def test_unmatched_registry_result_without_follow_up_option_forces_review(self):
        plan = WorkflowOrchestrator._registry_recovery_plan(
            [{
                "action": "targeted_verification",
                "result": {
                    "match_status": "mismatched",
                    "unresolved_fields": ["ssm_roc_rob_number"],
                },
            }],
            {
                "follow_up_candidates": [],
                "follow_up_draft_count": 0,
            },
        )

        self.assertEqual(plan["action"], "manual_review")

    def test_matched_registry_result_does_not_force_recovery_plan(self):
        plan = WorkflowOrchestrator._registry_recovery_plan(
            [{
                "action": "targeted_verification",
                "result": {
                    "match_status": "matched",
                    "unresolved_fields": [],
                },
            }],
            {"follow_up_candidates": [], "follow_up_draft_count": 0},
        )

        self.assertIsNone(plan)

    def test_clean_verification_with_null_severity_normalizes_to_minor(self):
        result = WorkflowOrchestrator._normalized_verification_severity({
            "legitimacy_status": "verified",
            "mismatch_severity": None,
            "critical_conflicts": [],
            "minor_mismatches": [],
            "moderate_mismatches": [],
            "critical_mismatches": [],
        })

        self.assertEqual(result, "minor")

    def test_verification_with_unclassified_findings_keeps_null_severity(self):
        result = WorkflowOrchestrator._normalized_verification_severity({
            "legitimacy_status": "verified",
            "mismatch_severity": None,
            "critical_conflicts": [],
            "minor_mismatches": [],
            "moderate_mismatches": [{"field_name": "contact_email"}],
            "critical_mismatches": [],
        })

        self.assertIsNone(result)

    def test_verification_with_missing_mismatch_lists_keeps_null_severity(self):
        result = WorkflowOrchestrator._normalized_verification_severity({
            "legitimacy_status": "verified",
            "mismatch_severity": None,
            "critical_conflicts": [],
        })

        self.assertIsNone(result)

    def setUp(self):
        self.case = SimpleNamespace(
            id=123,
            current_state="verification_completed",
        )
        self.case_query = SimpleNamespace(
            get_or_404=Mock(return_value=self.case),
            get=Mock(return_value=self.case),
        )
        # Patch the orchestrator module symbol instead of VendorCase.query:
        # Flask-SQLAlchemy resolves the query descriptor only inside an app context.
        self.query_patch = patch(
            "app.orchestrators.workflow_orchestrator.VendorCase",
            new=SimpleNamespace(query=self.case_query),
        )
        self.query_patch.start()
        self.addCleanup(self.query_patch.stop)
        self.db_patch = patch(
            "app.orchestrators.workflow_orchestrator.db",
            new=SimpleNamespace(session=SimpleNamespace(commit=Mock())),
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.uploaded_file_patch = patch(
            "app.orchestrators.workflow_orchestrator.UploadedFile",
            new=SimpleNamespace(
                query=SimpleNamespace(
                    filter_by=Mock(
                        return_value=SimpleNamespace(
                            first=Mock(return_value=object())
                        )
                    )
                )
            ),
        )
        self.uploaded_file_patch.start()
        self.addCleanup(self.uploaded_file_patch.stop)
        self.agentic_patch = patch.object(
            WorkflowOrchestrator,
            "_run_agentic_loop",
            return_value=([], "manual_review_required"),
        )
        self.agentic_patch.start()
        self.addCleanup(self.agentic_patch.stop)

    def test_no_documents_deterministically_waits_for_pic_upload(self):
        with (
            patch(
                "app.orchestrators.workflow_orchestrator.UploadedFile",
                new=SimpleNamespace(
                    query=SimpleNamespace(
                        filter_by=Mock(
                            return_value=SimpleNamespace(first=Mock(return_value=None))
                        )
                    )
                ),
            ),
            patch(
                "app.orchestrators.workflow_orchestrator.ExtractionOrchestrator.run"
            ) as extraction,
            patch(
                "app.orchestrators.workflow_orchestrator.ActionExecutor.execute",
                side_effect=lambda case_id, plan: (
                    setattr(self.case, "current_state", "awaiting_pic_upload")
                    or {
                        "status": "awaiting_pic_upload",
                        "current_state": "awaiting_pic_upload",
                    }
                ),
            ) as execute,
        ):
            self.case.current_state = "intake_received"
            result = WorkflowOrchestrator.run(123)

        extraction.assert_not_called()
        execute.assert_called_once()
        WorkflowOrchestrator._run_agentic_loop.assert_not_called()
        self.assertEqual(result["stages_run"], [])
        self.assertEqual(result["stopping_reason"], "awaiting_pic_upload")
        self.assertEqual(result["current_state"], "awaiting_pic_upload")

    def test_waiting_for_pic_upload_does_not_replan(self):
        self.case.current_state = "awaiting_pic_upload"
        with (
            patch(
                "app.orchestrators.workflow_orchestrator.UploadedFile",
                new=SimpleNamespace(
                    query=SimpleNamespace(
                        filter_by=Mock(
                            return_value=SimpleNamespace(first=Mock(return_value=None))
                        )
                    )
                ),
            ),
            patch(
                "app.orchestrators.workflow_orchestrator.ActionExecutor.execute"
            ) as execute,
        ):
            result = WorkflowOrchestrator.run(123)

        execute.assert_not_called()
        WorkflowOrchestrator._run_agentic_loop.assert_not_called()
        self.assertEqual(result["stopping_reason"], "awaiting_pic_upload")

    def test_unambiguous_missing_field_follow_up_bypasses_planner(self):
        self.case.current_state = "recovery_ready"
        snapshot = {
            "fields": [],
            "follow_up_candidates": [{
                "field_name": "bank_account_number",
                "availability": "not_found_in_reviewed_documents",
            }],
            "case_evidence_assessment": {
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
        }

        def create_draft(_case_id, plan):
            self.case.current_state = "pending_follow_up"
            return {"status": "completed", "email_draft_id": 77}

        with (
            patch(
                "app.services.next_action_planner.NextActionPlanner.case_snapshot",
                return_value=snapshot,
            ),
            patch(
                "app.orchestrators.workflow_orchestrator.ActionExecutor.execute",
                side_effect=create_draft,
            ) as execute,
        ):
            result = WorkflowOrchestrator.run(123)

        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[1]["action"], "request_information")
        WorkflowOrchestrator._run_agentic_loop.assert_not_called()
        self.assertEqual(result["stopping_reason"], "follow_up_created")
        self.assertEqual(result["current_state"], "pending_follow_up")

    def test_ambiguous_evidence_invokes_agentic_planner(self):
        self.case.current_state = "recovery_ready"
        with patch(
            "app.services.next_action_planner.NextActionPlanner.case_snapshot",
            return_value={
                "fields": [],
                "follow_up_candidates": [],
                "case_evidence_assessment": {
                    "ambiguous_fields": ["company_registered_number"],
                    "documents_added_after_last_structuring": False,
                },
            },
        ):
            result = WorkflowOrchestrator.run(123)

        WorkflowOrchestrator._run_agentic_loop.assert_called_once_with(123)
        self.assertEqual(result["stopping_reason"], "manual_review_required")

    def test_policy_success_then_routing_runs_in_order(self):
        calls = []

        def policy_stage(case_id):
            calls.append(("policy", case_id))
            self.case.current_state = "routing_in_progress"
            return {"risk_band": "low"}

        def routing_stage(case_id):
            calls.append(("routing", case_id))
            self.case.current_state = "completed"
            return {"decision_code": "complete_verified"}

        with (
            patch.object(
                WorkflowOrchestrator,
                "_run_policy_evaluation",
                side_effect=policy_stage,
            ),
            patch.object(
                WorkflowOrchestrator,
                "_run_routing",
                side_effect=routing_stage,
            ),
        ):
            result = WorkflowOrchestrator.run(123)

        self.assertEqual(calls, [("policy", 123), ("routing", 123)])
        self.assertEqual(
            result["stages_run"], ["policy_evaluation", "routing"]
        )
        self.assertEqual(result["current_state"], "completed")
        self.assertEqual(
            [
                (
                    entry["stage"],
                    entry["state_before"],
                    entry["state_after"],
                    entry["status"],
                )
                for entry in result["trace"]
            ],
            [
                (
                    "policy_evaluation",
                    "verification_completed",
                    "routing_in_progress",
                    "completed",
                ),
                (
                    "routing",
                    "routing_in_progress",
                    "completed",
                    "completed",
                ),
            ],
        )
        for entry in result["trace"]:
            self.assertIsInstance(entry["started_at"], str)
            self.assertIsInstance(entry["completed_at"], str)
            self.assertGreaterEqual(entry["duration_ms"], 0)

    def test_policy_failure_state_prevents_routing(self):
        def policy_failure(case_id):
            self.case.current_state = "manual_review_required"
            return {"status": "glm_failed", "error": "simulated failure"}

        with (
            patch.object(
                WorkflowOrchestrator,
                "_run_policy_evaluation",
                side_effect=policy_failure,
            ),
            patch.object(WorkflowOrchestrator, "_run_routing") as routing,
        ):
            result = WorkflowOrchestrator.run(123)

        routing.assert_not_called()
        self.assertEqual(result["stages_run"], ["policy_evaluation"])
        self.assertEqual(result["current_state"], "manual_review_required")
        self.assertEqual(result["trace"][0]["status"], "glm_failed")
        self.assertEqual(
            result["trace"][0]["state_after"], "manual_review_required"
        )

    def test_verification_success_flows_into_policy_then_routing(self):
        self.case.current_state = "field_alignment_completed"
        calls = []

        def verification_stage(case_id):
            calls.append("verification")
            self.case.current_state = "verification_completed"
            return {"legitimacy_status": "verified"}

        def policy_stage(case_id):
            calls.append("policy")
            self.case.current_state = "routing_in_progress"
            return {"risk_band": "low"}

        def routing_stage(case_id):
            calls.append("routing")
            self.case.current_state = "completed"
            return {"decision_code": "complete_verified"}

        with (
            patch.object(
                WorkflowOrchestrator,
                "_run_verification",
                side_effect=verification_stage,
            ),
            patch.object(
                WorkflowOrchestrator,
                "_run_policy_evaluation",
                side_effect=policy_stage,
            ),
            patch.object(
                WorkflowOrchestrator,
                "_run_routing",
                side_effect=routing_stage,
            ),
        ):
            result = WorkflowOrchestrator.run(123)

        self.assertEqual(calls, ["verification", "policy", "routing"])
        self.assertEqual(
            result["stages_run"],
            ["verification", "policy_evaluation", "routing"],
        )
        self.assertEqual(result["current_state"], "completed")
        self.assertEqual(
            [entry["stage"] for entry in result["trace"]],
            ["verification", "policy_evaluation", "routing"],
        )

    def test_terminal_case_does_not_run_any_stage(self):
        self.case.current_state = "completed"

        with (
            patch.object(WorkflowOrchestrator, "_run_verification") as verification,
            patch.object(WorkflowOrchestrator, "_run_policy_evaluation") as policy,
            patch.object(WorkflowOrchestrator, "_run_routing") as routing,
        ):
            result = WorkflowOrchestrator.run(123)

        verification.assert_not_called()
        policy.assert_not_called()
        routing.assert_not_called()
        self.assertEqual(result["status"], "already_terminal")
        self.assertEqual(result["current_state"], "completed")
        self.assertNotIn("trace", result)

    def test_normalize_decision_evidence_keeps_valid_pairs(self):
        result = WorkflowOrchestrator._normalize_decision_evidence([
            {"source": "  company_registry  ", "finding": "  Registration number matched  "},
            {"source": "policy_evaluation", "finding": "Risk band is low"},
        ])

        self.assertEqual(
            result,
            [
                {"source": "company_registry", "finding": "Registration number matched"},
                {"source": "policy_evaluation", "finding": "Risk band is low"},
            ],
        )

    def test_policy_normalization_removes_optional_outsourcing_risk(self):
        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "high",
                "risk_flags": [
                    {
                        "flag_type": "outsourcing_risk_flag",
                        "description": "Outsourcing details are missing.",
                        "field_name": "outsourcing_details",
                        "severity": "blocking",
                    }
                ],
                "blocking_issues": ["Missing outsourcing details"],
                "recoverable_issues": [],
                "blocking_issue_count": 1,
                "recoverable_issue_count": 0,
            },
            [
                SimpleNamespace(
                    field_name="outsource_any_part_of_services",
                    value_text="No",
                    normalized_value_text="No",
                )
            ],
            None,
        )

        self.assertEqual(result["risk_band"], "low")
        self.assertEqual(result["blocking_issue_count"], 0)
        self.assertEqual(result["risk_flags"], [])
        self.assertEqual(result["blocking_issues"], [])
        self.assertIn("No blocking or recoverable in-scope issues remain", result["policy_summary"])

    def test_policy_normalization_ignores_declaration_and_outsourcing_findings(self):
        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "critical",
                "risk_flags": [
                    {
                        "flag_type": "declaration_incomplete",
                        "description": "Declaration date is missing.",
                        "field_name": "declaration_date",
                        "severity": "blocking",
                    },
                    {
                        "flag_type": "outsourcing_risk_flag",
                        "description": "Outsourcing information is missing.",
                        "field_name": "outsourcing_details",
                        "severity": "blocking",
                    },
                ],
                "blocking_issues": [
                    "Declaration date is missing.",
                    "Outsourcing details are incomplete.",
                ],
                "recoverable_issues": [],
                "blocking_issue_count": 2,
                "recoverable_issue_count": 0,
            },
            [
                SimpleNamespace(
                    field_name="outsource_any_part_of_services",
                    value_text="Yes",
                    normalized_value_text="Yes",
                )
            ],
            None,
        )

        self.assertEqual(result["risk_band"], "low")
        self.assertEqual(result["blocking_issue_count"], 0)
        self.assertEqual(result["risk_flags"], [])
        self.assertEqual(result["blocking_issues"], [])

    def test_policy_normalization_preserves_unrelated_blocking_risk(self):
        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "high",
                "risk_flags": [
                    {
                        "flag_type": "identity_verification_gap",
                        "description": "Registration identity conflict.",
                        "field_name": "company_registered_number",
                        "severity": "blocking",
                    }
                ],
                "blocking_issues": ["Registration identity conflict"],
                "recoverable_issues": [],
                "blocking_issue_count": 1,
                "recoverable_issue_count": 0,
            },
            [
                SimpleNamespace(
                    field_name="outsource_any_part_of_services",
                    value_text="No",
                    normalized_value_text="No",
                )
            ],
            None,
        )

        self.assertEqual(result["risk_band"], "high")
        self.assertEqual(result["blocking_issue_count"], 1)

    def test_policy_normalization_ignores_external_gaps_after_internal_match(self):
        fields = [
            SimpleNamespace(
                field_name="tax_sst_gst_number",
                value_text="B16-1808-32001114",
                normalized_value_text="B16-1808-32001114",
                status="matched_with_evidence",
            ),
            SimpleNamespace(
                field_name="iso_certification_available",
                value_text="Yes",
                normalized_value_text="Yes",
                status="matched_with_evidence",
            ),
            SimpleNamespace(
                field_name="iso_certification_number",
                value_text="MY-ISO9001-2024-8831",
                normalized_value_text="MY-ISO9001-2024-8831",
                status="filled_from_supporting_docs",
            ),
            SimpleNamespace(
                field_name="iso_certification_expiry_date",
                value_text="09 January 2099",
                normalized_value_text="09 January 2099",
                status="filled_from_supporting_docs",
            ),
        ]
        registry_match = {
            "match_status": "matched",
            "matched_vendor_number": "V-10004",
            "field_comparisons": [{
                "vendor_number": "V-10004",
                "field_results": [{
                    "field_name": "tax_sst_gst_number",
                    "match": "exact",
                }],
            }],
        }

        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "medium",
                "risk_flags": [{
                    "flag_type": "certification_unverifiable",
                    "description": "ISO authenticity was not externally authenticated.",
                    "field_name": "iso_certification_number",
                    "severity": "recoverable",
                }],
                "blocking_issues": [],
                "recoverable_issues": [
                    "Tax identifier was not externally verified.",
                    "ISO certificate authenticity could not be externally confirmed.",
                ],
                "blocking_issue_count": 0,
                "recoverable_issue_count": 2,
            },
            fields,
            None,
            registry_match,
        )

        self.assertEqual(result["risk_band"], "low")
        self.assertEqual(result["blocking_issue_count"], 0)
        self.assertEqual(result["recoverable_issue_count"], 0)
        self.assertEqual(result["blocking_issues"], [])
        self.assertEqual(result["recoverable_issues"], [])
        self.assertEqual(result["risk_flags"][0]["severity"], "warning")
        self.assertIn("no external verification is claimed", result["policy_summary"])

    def test_policy_normalization_preserves_actual_iso_expiry_issue(self):
        fields = [
            SimpleNamespace(
                field_name="iso_certification_available",
                value_text="Yes",
                normalized_value_text="Yes",
                status="matched_with_evidence",
            ),
            SimpleNamespace(
                field_name="iso_certification_number",
                value_text="ISO-12345",
                normalized_value_text="ISO-12345",
                status="filled_from_supporting_docs",
            ),
            SimpleNamespace(
                field_name="iso_certification_expiry_date",
                value_text="01 January 2000",
                normalized_value_text="01 January 2000",
                status="matched_with_evidence",
            ),
        ]

        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "high",
                "risk_flags": [{
                    "flag_type": "certification_expired",
                    "description": "ISO certificate expired on 01 January 2000.",
                    "field_name": "iso_certification_expiry_date",
                    "severity": "blocking",
                }],
                "blocking_issues": ["ISO certificate expired."],
                "recoverable_issues": [],
                "blocking_issue_count": 1,
                "recoverable_issue_count": 0,
            },
            fields,
            None,
        )

        self.assertEqual(result["risk_band"], "high")
        self.assertEqual(result["blocking_issue_count"], 1)
        self.assertEqual(result["blocking_issues"], ["ISO certificate expired."])

    def test_policy_normalization_keeps_tax_verification_gap_without_exact_registry_match(self):
        fields = [
            SimpleNamespace(
                field_name="tax_sst_gst_number",
                value_text="TAX-12345",
                normalized_value_text="TAX-12345",
                status="matched_with_evidence",
            )
        ]
        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "medium",
                "risk_flags": [{
                    "flag_type": "tax_identifier_gap",
                    "description": "Tax identifier was not externally verified.",
                    "field_name": "tax_sst_gst_number",
                    "severity": "recoverable",
                }],
                "blocking_issues": [],
                "recoverable_issues": [
                    "Tax identifier was not externally verified."
                ],
                "blocking_issue_count": 0,
                "recoverable_issue_count": 1,
            },
            fields,
            None,
        )

        self.assertEqual(result["risk_band"], "medium")
        self.assertEqual(result["recoverable_issue_count"], 1)
        self.assertEqual(len(result["recoverable_issues"]), 1)

    def test_policy_normalization_clears_revenue_follow_up_after_vendor_response(self):
        revenue = SimpleNamespace(
            field_name="latest_annual_revenue_range",
            value_text="RM1M-RM5M",
            normalized_value_text="RM1M-RM5M",
            notes="Vendor response received and transcribed by PIC.",
        )
        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "medium",
                "risk_flags": [{
                    "flag_type": "low_confidence_case",
                    "description": "Latest annual revenue range is low confidence.",
                    "field_name": "latest_annual_revenue_range",
                    "severity": "recoverable",
                }],
                "blocking_issues": [],
                "recoverable_issues": [
                    "Clarify latest annual revenue range"
                ],
                "blocking_issue_count": 0,
                "recoverable_issue_count": 2,
            },
            [revenue],
            None,
        )

        self.assertEqual(result["risk_flags"], [])
        self.assertEqual(result["recoverable_issues"], [])
        self.assertEqual(result["recoverable_issue_count"], 0)

    def test_policy_normalization_clears_revenue_follow_up_after_pic_correction(self):
        revenue = SimpleNamespace(
            field_name="latest_annual_revenue_range",
            value_text="50-100k",
            normalized_value_text="50-100k",
            notes="PIC manual correction; not independently verified.",
        )
        result = WorkflowOrchestrator._normalize_policy_result(
            {
                "risk_band": "medium",
                "risk_flags": [{
                    "flag_type": "low_confidence_case",
                    "description": "Latest annual revenue range has low confidence.",
                    "field_name": "latest_annual_revenue_range",
                    "severity": "recoverable",
                }],
                "blocking_issues": [],
                "recoverable_issues": ["Clarify latest annual revenue range"],
                "blocking_issue_count": 0,
                "recoverable_issue_count": 1,
            },
            [revenue],
            None,
        )

        self.assertEqual(result["risk_flags"], [])
        self.assertEqual(result["recoverable_issues"], [])
        self.assertEqual(result["recoverable_issue_count"], 0)

    def test_follow_up_without_unresolved_eligible_fields_uses_safety_gate(self):
        field_values = [
            SimpleNamespace(
                field_name="latest_annual_revenue_range",
                value_text="RM1M-RM5M",
                normalized_value_text="RM1M-RM5M",
                notes="Vendor response received and transcribed by PIC.",
            )
        ]
        with (
            patch(
                "app.orchestrators.workflow_orchestrator.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(
                    filter_by=Mock(return_value=SimpleNamespace(all=Mock(return_value=field_values)))
                )),
            ),
            patch.object(
                WorkflowOrchestrator,
                "_apply_safety_gate",
                return_value=(
                    "complete_verified",
                    "Deterministic safety checks passed.",
                    "Proceed with vendor onboarding",
                    {"allowed": True},
                ),
            ) as safety_gate,
        ):
            result = WorkflowOrchestrator._enforce_follow_up_eligibility(
                31,
                "follow_up_required",
                "Request revenue.",
                "Request clarification",
                0.9,
            )

        self.assertEqual(result[0], "complete_verified")
        safety_gate.assert_called_once()

    def test_policy_prompt_excludes_signature_and_chop_from_scope(self):
        from app.prompts.policy_evaluation import POLICY_EVALUATION_PROMPT

        self.assertIn(
            "ignore every other form field",
            POLICY_EVALUATION_PROMPT.lower(),
        )
        self.assertIn("signature/signatory fields", POLICY_EVALUATION_PROMPT.lower())

    def test_verification_prompt_ignores_external_tax_gaps_after_exact_registry_match(self):
        from app.prompts.verification import VERIFICATION_PROMPT

        prompt = VERIFICATION_PROMPT.lower()
        self.assertIn(
            "when tax_sst_gst_number has an exact internal registry match",
            prompt,
        )
        self.assertIn(
            "do not lower confidence, report a gap, or recommend follow-up solely",
            prompt,
        )

    def test_follow_up_prompt_ignores_external_tax_gaps_after_registry_match(self):
        from app.prompts.follow_up import FOLLOW_UP_PROMPT

        prompt = FOLLOW_UP_PROMPT.lower()
        self.assertIn("internal registry result shows an exact match", prompt)
        self.assertIn("partially verified, not assessed, or unavailable", prompt)
        self.assertIn("{internal_registry}", FOLLOW_UP_PROMPT)
        self.assertIn("{external_verification}", FOLLOW_UP_PROMPT)
        rendered = FOLLOW_UP_PROMPT.format(
            routing_decision="{}",
            unresolved_fields="[]",
            recoverable_issues=0,
            evidence_conflicts="[]",
            internal_registry="{}",
            external_verification="{}",
            vendor_contact="{}",
        )
        self.assertIn("INTERNAL REGISTRY RESULT:", rendered)

    def test_routing_prompt_completes_verified_low_risk_minor_gaps(self):
        from app.prompts.routing import ROUTING_PROMPT

        prompt = ROUTING_PROMPT.lower()
        self.assertIn(
            "a minor mismatch severity alone is not a reason for manual review",
            prompt,
        )
        self.assertIn(
            "zero unresolved fields, choose complete_verified and confidence >= 0.8",
            prompt,
        )
        self.assertIn(
            "do not recommend manual review solely because minor mismatches",
            prompt,
        )

    def test_normalize_decision_evidence_discards_malformed_items(self):
        result = WorkflowOrchestrator._normalize_decision_evidence([
            {"source": "registry", "finding": "matched"},
            {"source": "", "finding": "empty source"},
            {"source": "registry", "finding": "  "},
            {"source": None, "finding": "missing source"},
            "not an evidence object",
        ])

        self.assertEqual(result, [{"source": "registry", "finding": "matched"}])

    def test_normalize_decision_evidence_handles_non_list_output(self):
        self.assertEqual(WorkflowOrchestrator._normalize_decision_evidence(None), [])
        self.assertEqual(WorkflowOrchestrator._normalize_decision_evidence({"source": "x"}), [])

    def test_extraction_trace_summary_omits_extracted_content(self):
        result = WorkflowOrchestrator._summarize_extraction_result({
            "current_state": "intake_classified",
            "files_processed": 1,
            "zip_files_extracted": 0,
            "extraction_results": [{
                "file_id": 7,
                "filename": "private.pdf",
                "status": "completed",
                "full_text": "private document contents",
            }],
        })

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("private.pdf", str(result))
        self.assertNotIn("private document contents", str(result))

    def test_low_ocr_quality_is_reported_as_pic_warning_not_stage_failure(self):
        result = WorkflowOrchestrator._summarize_extraction_result({
            "current_state": "intake_classified",
            "files_processed": 1,
            "zip_files_extracted": 0,
            "extraction_results": [{
                "file_id": 8,
                "filename": "private.pdf",
                "extraction_method": "paddleocr",
                "status": "completed",
                "ocr_quality": {
                    "quality_status": "low_confidence",
                    "average_confidence": 0.51,
                    "minimum_confidence": 0.22,
                    "low_confidence_block_count": 3,
                },
            }],
        })

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["ocr_warnings"][0]["quality_status"], "low_confidence")
        self.assertNotIn("private.pdf", str(result))

    def test_low_ocr_quality_requests_pic_correction_not_vendor_information(self):
        extraction = WorkflowOrchestrator._summarize_extraction_result({
            "extraction_results": [{
                "file_id": 8,
                "extraction_method": "paddleocr",
                "status": "completed",
                "ocr_quality": {"quality_status": "low_confidence"},
            }],
        })

        self.assertTrue(WorkflowOrchestrator._needs_pic_correction(
            {"fields": []},
            {"extraction": extraction},
        ))
        self.assertIsNone(WorkflowOrchestrator._deterministic_recovery_plan({
            "follow_up_candidates": [{
                "field_name": "bank_account_number",
                "status": "low_confidence",
                "availability": "not_found_in_reviewed_documents",
            }],
            "case_evidence_assessment": {
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
        }))

    def test_low_ocr_quality_holds_case_before_routing(self):
        self.case.current_state = "intake_received"

        def advance_state(state, result):
            self.case.current_state = state
            return result

        def request_clearer_document(_case_id, _plan):
            self.case.current_state = "awaiting_pic_upload"
            return {
                "status": "awaiting_pic_upload",
                "current_state": "awaiting_pic_upload",
            }

        with (
            patch(
                "app.orchestrators.workflow_orchestrator.ExtractionOrchestrator.run",
                return_value={
                    "extraction_results": [{
                        "file_id": 8,
                        "extraction_method": "paddleocr",
                        "status": "completed",
                        "ocr_quality": {"quality_status": "unreadable"},
                    }],
                },
            ),
            patch(
                "app.orchestrators.workflow_orchestrator.FormService.structure_form",
                side_effect=lambda _case_id: advance_state(
                    "field_alignment_completed", {"status": "completed"}
                ),
            ),
            patch.object(
                WorkflowOrchestrator,
                "_run_verification",
                side_effect=lambda _case_id: advance_state(
                    "verification_completed", {"status": "completed"}
                ),
            ),
            patch.object(
                WorkflowOrchestrator,
                "_run_policy_evaluation",
                side_effect=lambda _case_id: advance_state(
                    "routing_in_progress", {"status": "completed"}
                ),
            ),
            patch.object(WorkflowOrchestrator, "_run_routing") as routing,
            patch(
                "app.orchestrators.workflow_orchestrator.ActionExecutor.execute",
                side_effect=request_clearer_document,
            ),
        ):
            result = WorkflowOrchestrator.run(123)

        routing.assert_not_called()
        self.assertEqual(result["current_state"], "awaiting_pic_upload")
        self.assertEqual(result["stopping_reason"], "awaiting_pic_upload")

    def test_technical_ocr_failure_is_distinct_and_retryable(self):
        result = WorkflowOrchestrator._summarize_extraction_result({
            "extraction_results": [{
                "file_id": 9,
                "extraction_method": "paddleocr",
                "status": "ocr_error: temporary engine failure",
                "ocr_quality": {"quality_status": "unreadable"},
            }],
        })

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])
        self.assertEqual(
            result["technical_failures"][0]["status"],
            "ocr_error: temporary engine failure",
        )
        self.assertEqual(result["ocr_warnings"][0]["quality_status"], "unreadable")

    def test_unavailable_ocr_is_not_automatically_retried(self):
        result = WorkflowOrchestrator._summarize_extraction_result({
            "extraction_results": [{
                "file_id": 10,
                "extraction_method": "paddleocr",
                "status": "ocr_unavailable",
                "ocr_quality": {"quality_status": "unreadable"},
            }],
        })

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])


if __name__ == "__main__":
    unittest.main()
