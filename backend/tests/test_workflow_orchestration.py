import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator


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

    def test_no_documents_routes_case_to_agentic_upload_decision(self):
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
            patch.object(
                WorkflowOrchestrator,
                "_run_agentic_loop",
                return_value=([], "awaiting_pic_upload"),
            ) as agentic_loop,
        ):
            result = WorkflowOrchestrator.run(123)

        extraction.assert_not_called()
        agentic_loop.assert_called_once_with(123)
        self.assertEqual(result["stages_run"], [])
        self.assertEqual(result["stopping_reason"], "awaiting_pic_upload")

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
        self.assertEqual(result["current_state"], "processing_failed")
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

        self.assertEqual(result["risk_band"], "medium")
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

    def test_policy_prompt_marks_signature_and_chop_optional(self):
        from app.prompts.policy_evaluation import POLICY_EVALUATION_PROMPT

        self.assertIn(
            "authorized signature present and company chop present are optional",
            POLICY_EVALUATION_PROMPT.lower(),
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


if __name__ == "__main__":
    unittest.main()
