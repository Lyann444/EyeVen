import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from app.orchestrators.reprocessing_orchestrator import ReprocessingOrchestrator
from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator
from app.services.action_executor import ActionExecutor, ActionExecutorError
from app.services.glm_service import GlmMalformedOutputError
from app.services.internal_registry_service import InternalRegistryService
from app.services.next_action_planner import (
    NextActionPlanner,
    NextActionPlannerError,
)


def plan(action, target_fields=None, target_stage=None):
    return {
        "action": action,
        "reason": "Resolve an identified workflow issue.",
        "target_fields": target_fields or [],
        "target_stage": target_stage,
    }


class NextActionPlannerTests(unittest.TestCase):
    def setUp(self):
        case = SimpleNamespace(current_state="verification_completed")
        self.case_patch = patch(
            "app.services.action_executor.VendorCase",
            new=SimpleNamespace(
                query=SimpleNamespace(get_or_404=Mock(return_value=case))
            ),
        )
        self.case_patch.start()
        self.addCleanup(self.case_patch.stop)

    def test_missing_information_selects_follow_up_action(self):
        snapshot = {
            "fields": [{
                "field_name": "bank_account_number",
                "status": "missing",
                "value_present": False,
                "evidence_available": False,
                "availability": "not_found_in_reviewed_documents",
                "follow_up_eligible": True,
            }],
            "available_evidence": [{"file_id": 1}],
            "unchanged_successful_stages": [],
        }
        response = plan("request_information")
        with (
            patch.object(NextActionPlanner, "case_snapshot", return_value=snapshot),
            patch(
                "app.services.next_action_planner.GlmService.call_glm",
                return_value=response,
            ) as call_glm,
        ):
            result = NextActionPlanner.plan(12, [])

        self.assertEqual(result["action"], "request_information")
        self.assertIn("bank_account_number", call_glm.call_args.kwargs["prompt"])

    def test_validates_internal_registry_targeted_verification(self):
        result = NextActionPlanner.validate_plan(
            plan("targeted_verification", ["company_registered_number"])
        )
        self.assertEqual(result["target_fields"], ["company_registered_number"])

    def test_failed_extraction_can_select_extraction_reprocessing(self):
        failed_extraction = {
            "action": "reprocess_stage",
            "target_stage": "extraction",
            "target_fields": [],
            "outcome": "failed",
            "reason": "Prior extraction stage returned failed.",
        }
        response = plan("reprocess_stage", target_stage="extraction")
        with (
            patch.object(
                NextActionPlanner,
                "case_snapshot",
                side_effect=lambda _case_id, prior: {
                    "prior_actions": prior,
                    "fields": [],
                    "available_evidence": [],
                    "unchanged_successful_stages": [],
                },
            ),
            patch(
                "app.services.next_action_planner.GlmService.call_glm",
                return_value=response,
            ) as call_glm,
        ):
            result = NextActionPlanner.plan(12, [failed_extraction])

        self.assertEqual(result["target_stage"], "extraction")
        self.assertIn("Prior extraction stage returned failed", call_glm.call_args.kwargs["prompt"])

    def test_reprocessing_is_blocked_after_automatic_retry_exhaustion(self):
        snapshot = {
            "fields": [],
            "prior_actions": [{
                "action": "reprocess_stage",
                "target_stage": "verification",
                "outcome": "failed",
            }],
            "retry_exhausted_stages": ["verification"],
            "unchanged_successful_stages": [],
        }

        with self.assertRaisesRegex(
            NextActionPlannerError, "automatic retry is exhausted"
        ):
            NextActionPlanner.validate_plan_for_case(
                plan("reprocess_stage", target_stage="verification"),
                snapshot,
            )

    def test_invalid_request_information_targets_fall_back_to_snapshot_decision(self):
        snapshot = {
            "fields": [{
                "field_name": "latest_annual_revenue_range",
                "status": "low_confidence",
                "value": "50-100k",
                "value_present": True,
                "evidence_available": False,
                "notes": "PIC manual correction; not independently verified.",
                "availability": "not_found_in_reviewed_documents",
                "follow_up_eligible": False,
            }],
            "available_evidence": [{"file_id": 1}],
            "prior_actions": [],
            "case_evidence_assessment": {
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
            "unchanged_successful_stages": [],
            "policy": None,
            "verification": None,
        }
        invalid_response = {
            **plan("request_information", ["latest_annual_revenue_range"]),
            "target_stage": "routing",
        }

        with (
            patch.object(NextActionPlanner, "case_snapshot", return_value=snapshot),
            patch(
                "app.services.next_action_planner.GlmService.call_glm",
                return_value=invalid_response,
            ),
        ):
            result = NextActionPlanner.plan(12, [])

        self.assertEqual(result["action"], "finish")
        self.assertEqual(result["target_fields"], [])
        self.assertIsNone(result["target_stage"])

    def test_rejects_unknown_action_and_invalid_targets(self):
        for invalid_plan in (
            plan("run_shell"),
            plan("targeted_verification", ["bank_account_number"]),
            plan("reprocess_stage", target_stage="unsupported"),
            plan("finish", ["company_registered_number"]),
            plan("finish", ["company_registered_number", "company_registered_number"]),
        ):
            with self.subTest(invalid_plan=invalid_plan):
                with self.assertRaises(NextActionPlannerError):
                    NextActionPlanner.validate_plan(invalid_plan)

    def test_routing_is_a_supported_reprocessing_stage(self):
        result = NextActionPlanner.validate_plan(
            plan("reprocess_stage", target_stage="routing")
        )
        self.assertEqual(result["target_stage"], "routing")

    def test_rejects_vendor_follow_up_when_field_has_evidence(self):
        with self.assertRaisesRegex(NextActionPlannerError, "reviewed"):
            NextActionPlanner.validate_plan_for_case(
                plan("request_information"),
                {
                    "fields": [{
                        "field_name": "tax_sst_gst_number",
                        "status": "missing",
                        "value_present": False,
                        "evidence_available": True,
                        "availability": "evidence_found_for_field",
                        "follow_up_eligible": True,
                    }],
                    "available_evidence": [{"file_id": 1}],
                },
            )

    def test_rejects_vendor_follow_up_until_documents_are_reviewed(self):
        with self.assertRaisesRegex(NextActionPlannerError, "reviewed"):
            NextActionPlanner.validate_plan_for_case(
                plan("request_information"),
                {
                    "fields": [{
                        "field_name": "tax_sst_gst_number",
                        "status": "missing",
                        "value_present": False,
                        "evidence_available": False,
                        "availability": "documents_not_yet_reviewed",
                        "follow_up_eligible": True,
                    }],
                    "available_evidence": [{"file_id": 1}],
                },
            )

    def test_pic_upload_requires_no_documents_and_missing_required_fields(self):
        missing_field = {
            "field_name": "tax_sst_gst_number",
            "status": "missing",
            "value_present": False,
            "evidence_available": False,
            "availability": "no_documents_submitted",
            "follow_up_eligible": True,
        }
        NextActionPlanner.validate_plan_for_case(
            plan("request_pic_upload"),
            {"fields": [missing_field], "available_evidence": []},
        )
        with self.assertRaisesRegex(
            NextActionPlannerError, "only allowed when no case documents"
        ):
            NextActionPlanner.validate_plan_for_case(
                plan("request_pic_upload"),
                {"fields": [missing_field], "available_evidence": [{"file_id": 1}]},
            )

    def test_evidence_based_fallback_requests_pic_upload_when_no_documents_exist(self):
        fallback = NextActionPlanner._evidence_based_plan({
            "fields": [{
                "field_name": "tax_sst_gst_number",
                "status": "missing",
                "value_present": False,
                "evidence_available": False,
                "availability": "no_documents_submitted",
                "follow_up_eligible": True,
            }],
            "available_evidence": [],
            "prior_actions": [],
            "case_evidence_assessment": {
                "document_count": 0,
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
            "unchanged_successful_stages": [],
        })

        self.assertEqual(fallback["action"], "request_pic_upload")

    def test_evidence_based_fallback_reprocesses_new_documents_before_follow_up(self):
        fallback = NextActionPlanner._evidence_based_plan({
            "fields": [{
                "field_name": "tax_sst_gst_number",
                "status": "missing",
                "value_present": False,
                "evidence_available": False,
                "availability": "new_document_needs_processing",
                "follow_up_eligible": True,
            }],
            "available_evidence": [{"file_id": 1}],
            "prior_actions": [],
            "case_evidence_assessment": {
                "document_count": 1,
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": True,
            },
            "unchanged_successful_stages": [],
        })

        self.assertEqual(fallback["action"], "reprocess_stage")
        self.assertEqual(fallback["target_stage"], "extraction")

    def test_evidence_based_fallback_requests_only_information_absent_from_reviewed_docs(self):
        field = {
            "field_name": "tax_sst_gst_number",
            "status": "missing",
            "value_present": False,
            "evidence_available": False,
            "availability": "not_found_in_reviewed_documents",
            "follow_up_eligible": True,
        }
        follow_up = NextActionPlanner._evidence_based_plan({
            "fields": [field],
            "available_evidence": [{"file_id": 1}],
            "prior_actions": [],
            "case_evidence_assessment": {
                "document_count": 1,
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
            "unchanged_successful_stages": [],
        })
        manual_review = NextActionPlanner._evidence_based_plan({
            "fields": [field],
            "available_evidence": [{"file_id": 1}],
            "prior_actions": [],
            "case_evidence_assessment": {
                "document_count": 1,
                "ambiguous_fields": ["tax_sst_gst_number"],
                "documents_added_after_last_structuring": False,
            },
            "unchanged_successful_stages": [],
        })

        self.assertEqual(follow_up["action"], "request_information")
        self.assertEqual(manual_review["action"], "manual_review")

    def test_evidence_based_fallback_retries_failed_stage_and_avoids_repeating_success(self):
        failed = NextActionPlanner._evidence_based_plan({
            "fields": [],
            "available_evidence": [],
            "prior_actions": [{
                "action": "reprocess_stage",
                "target_stage": "verification",
                "outcome": "failed",
            }],
            "case_evidence_assessment": {},
            "unchanged_successful_stages": ["verification"],
        })
        unchanged = NextActionPlanner._evidence_based_plan({
            "fields": [{
                "field_name": "tax_sst_gst_number",
                "status": "missing",
                "value_present": False,
                "evidence_available": True,
                "availability": "evidence_found_for_field",
                "follow_up_eligible": True,
                "notes": "",
            }],
            "available_evidence": [{"file_id": 1}],
            "prior_actions": [],
            "case_evidence_assessment": {
                "ambiguous_fields": [],
                "documents_added_after_last_structuring": False,
            },
            "unchanged_successful_stages": ["form_structuring"],
        })

        self.assertEqual(failed["action"], "reprocess_stage")
        self.assertEqual(failed["target_stage"], "verification")
        self.assertEqual(unchanged["action"], "manual_review")

    def test_reprocessing_reason_tracks_failure_new_document_and_manual_correction(self):
        self.assertEqual(
            ActionExecutor._reprocessing_reason(
                {"prior_actions": [{
                    "action": "reprocess_stage",
                    "target_stage": "verification",
                    "outcome": "failed",
                }]},
                "verification",
            ),
            "recoverable_failure",
        )
        self.assertEqual(
            ActionExecutor._reprocessing_reason(
                {
                    "prior_actions": [],
                    "case_evidence_assessment": {
                        "documents_added_after_last_structuring": True,
                    },
                },
                "extraction",
            ),
            "new_document_uploaded",
        )
        self.assertEqual(
            ActionExecutor._reprocessing_reason(
                {
                    "prior_actions": [],
                    "case_evidence_assessment": {},
                    "fields": [{
                        "follow_up_eligible": True,
                        "notes": "PIC manual correction; not independently verified.",
                    }],
                },
                "verification",
            ),
            "manual_correction",
        )

    def test_finish_is_rejected_while_required_information_is_unresolved(self):
        with self.assertRaisesRegex(NextActionPlannerError, "remain unresolved"):
            NextActionPlanner.validate_plan_for_case(
                plan("finish"),
                {
                    "fields": [{
                        "field_name": "tax_sst_gst_number",
                        "status": "missing",
                        "value_present": False,
                        "follow_up_eligible": True,
                    }],
                },
            )

    def test_malformed_planner_json_is_rejected(self):
        with self.assertRaises(GlmMalformedOutputError):
            from app.services.glm_service import GlmService

            GlmService._parse_json_response("{invalid")

    def test_targeted_verification_uses_only_internal_registry(self):
        with (
            patch(
                "app.services.action_executor.InternalRegistryService.match_vendor",
                return_value={
                    "match_status": "matched",
                    "matched_vendor_number": "V-10001",
                    "field_comparisons": [],
                },
            ) as match_vendor,
            patch(
                "app.services.action_executor.EyeVenFormFieldValue"
            ) as model,
        ):
            model.query.filter_by.return_value.all.return_value = []
            result = ActionExecutor.execute(
                12, plan("targeted_verification", ["ssm_roc_rob_number"])
            )

        match_vendor.assert_called_once_with(
            12, target_fields=["ssm_roc_rob_number"]
        )
        self.assertEqual(result["source"], "internal_demo_registry")
        self.assertFalse(result["external_verification_performed"])

    def test_unmatched_targeted_verification_persists_unverifiable_field(self):
        field = SimpleNamespace(
            field_name="ssm_roc_rob_number",
            status="provided_in_form",
            notes=None,
        )
        registry_result = {
            "match_status": "no_match",
            "matched_vendor_number": None,
            "field_comparisons": [
                {
                    "vendor_number": "V-10004",
                    "field_results": [
                        {
                            "field_name": "ssm_roc_rob_number",
                            "match": "mismatch",
                        }
                    ],
                }
            ],
        }
        with (
            patch(
                "app.services.action_executor.InternalRegistryService.match_vendor",
                return_value=registry_result,
            ),
            patch(
                "app.services.action_executor.EyeVenFormFieldValue"
            ) as model,
            patch("app.services.action_executor.db.session.commit") as commit,
        ):
            model.query.filter_by.return_value.all.return_value = [field]
            result = ActionExecutor.execute(
                12, plan("targeted_verification", ["ssm_roc_rob_number"])
            )

        self.assertEqual(field.status, "unverifiable")
        self.assertIn("not an external registry check", field.notes)
        self.assertEqual(result["unresolved_fields"], ["ssm_roc_rob_number"])
        commit.assert_called_once()

    def test_executor_rejects_unknown_action_and_targets(self):
        with self.assertRaises(ActionExecutorError):
            ActionExecutor.execute(12, plan("unknown_action"))
        with self.assertRaises(ActionExecutorError):
            ActionExecutor.execute(
                12, plan("targeted_verification", ["bank_account_number"])
            )
        with self.assertRaises(ActionExecutorError):
            ActionExecutor.execute(
                12, plan("reprocess_stage", target_stage="unsupported")
            )

    def test_executor_blocks_mutating_action_for_terminal_case(self):
        terminal_case = SimpleNamespace(current_state="completed")
        with patch(
            "app.services.action_executor.VendorCase",
            new=SimpleNamespace(
                query=SimpleNamespace(get_or_404=Mock(return_value=terminal_case))
            ),
        ):
            result = ActionExecutor.execute(12, plan("request_information"))
        self.assertEqual(result["status"], "blocked_terminal_case")

    def test_pic_upload_action_persists_waiting_state(self):
        case = SimpleNamespace(current_state="verification_completed")
        with (
            patch(
                "app.services.action_executor.VendorCase",
                new=SimpleNamespace(
                    query=SimpleNamespace(get_or_404=Mock(return_value=case))
                ),
            ),
            patch("app.services.action_executor.db.session.commit") as commit,
        ):
            result = ActionExecutor.execute(12, plan("request_pic_upload"))

        self.assertEqual(result, {
            "status": "awaiting_pic_upload",
            "current_state": "awaiting_pic_upload",
        })
        self.assertEqual(case.current_state, "awaiting_pic_upload")
        commit.assert_called_once()

    def test_internal_registry_can_match_one_targeted_identifier(self):
        field = SimpleNamespace(
            field_name="company_registered_number",
            value_text="202001012345",
        )
        with patch(
            "app.services.internal_registry_service.EyeVenFormFieldValue"
        ) as model:
            model.query.filter_by.return_value.all.return_value = [field]
            result = InternalRegistryService.match_vendor(
                12, target_fields=["company_registered_number"]
            )

        self.assertEqual(result["match_status"], "matched")
        self.assertEqual(result["matched_vendor_number"], "V-10001")

    def test_internal_registry_normalizes_punctuation_for_apex_record(self):
        fields = [
            SimpleNamespace(
                field_name="company_registered_name",
                value_text="Apex Office Supplies Sdn.Bhd.",
            ),
            SimpleNamespace(
                field_name="company_registered_number",
                value_text="201901045678 (1355008-X)",
            ),
            SimpleNamespace(
                field_name="ssm_roc_rob_number",
                value_text="201901045678 (1355008-X)",
            ),
            SimpleNamespace(
                field_name="tax_sst_gst_number",
                value_text="B16-1808-32001114",
            ),
        ]
        with patch(
            "app.services.internal_registry_service.EyeVenFormFieldValue"
        ) as model:
            model.query.filter_by.return_value.all.return_value = fields
            result = InternalRegistryService.match_vendor(12)

        self.assertEqual(result["match_status"], "matched")
        self.assertEqual(result["matched_vendor_number"], "V-10004")
        apex_match = next(
            item for item in result["field_comparisons"]
            if item["vendor_number"] == "V-10004"
        )
        self.assertEqual(
            [field["match"] for field in apex_match["field_results"]],
            ["exact", "exact", "exact", "exact"],
        )

    def test_internal_registry_ignores_optional_parenthesized_legacy_suffix(self):
        fields = [
            SimpleNamespace(
                field_name="company_registered_name",
                value_text="Apex Office Supplies Sdn. Bhd.",
            ),
            SimpleNamespace(
                field_name="company_registered_number",
                value_text="201901045678",
            ),
            SimpleNamespace(
                field_name="ssm_roc_rob_number",
                value_text="201901045678 (1355008-X)",
            ),
            SimpleNamespace(
                field_name="tax_sst_gst_number",
                value_text="B16-1808-32001114",
            ),
        ]
        with patch(
            "app.services.internal_registry_service.EyeVenFormFieldValue"
        ) as model:
            model.query.filter_by.return_value.all.return_value = fields
            result = InternalRegistryService.match_vendor(12)

        self.assertEqual(result["match_status"], "matched")
        self.assertEqual(result["matched_vendor_number"], "V-10004")
        apex_match = next(
            item for item in result["field_comparisons"]
            if item["vendor_number"] == "V-10004"
        )
        self.assertEqual(
            [field["match"] for field in apex_match["field_results"]],
            ["exact", "exact", "exact", "exact"],
        )

    def test_internal_registry_still_detects_different_primary_registration_number(self):
        fields = [
            SimpleNamespace(
                field_name="company_registered_number",
                value_text="201901045679 (1355008-X)",
            ),
            SimpleNamespace(
                field_name="ssm_roc_rob_number",
                value_text="201901045679 (1355008-X)",
            ),
        ]
        with patch(
            "app.services.internal_registry_service.EyeVenFormFieldValue"
        ) as model:
            model.query.filter_by.return_value.all.return_value = fields
            result = InternalRegistryService.match_vendor(12)

        self.assertNotEqual(result["match_status"], "matched")
        apex_match = next(
            item for item in result["field_comparisons"]
            if item["vendor_number"] == "V-10004"
        )
        self.assertEqual(
            [field["match"] for field in apex_match["field_results"]],
            ["mismatch", "not_provided", "mismatch", "not_provided"],
        )

    def test_agentic_trace_result_excludes_error_and_document_content(self):
        result = WorkflowOrchestrator._safe_agentic_result({
            "status": "failed",
            "error": "sensitive provider response",
            "full_text": "private document contents",
            "stage": "extraction",
        })

        self.assertEqual(result, {"status": "failed", "stage": "extraction"})


class AgenticLoopTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["MAX_AGENTIC_ACTIONS"] = 3
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.case = SimpleNamespace(current_state="pending_follow_up")
        self.query_patch = patch(
            "app.orchestrators.workflow_orchestrator.VendorCase",
            new=SimpleNamespace(query=SimpleNamespace(get=Mock(return_value=self.case))),
        )
        self.query_patch.start()
        self.addCleanup(self.query_patch.stop)

    @staticmethod
    def snapshot(state="pending_follow_up"):
        return {
            "current_state": state,
            "fields": [{"field_name": "bank_account_number", "status": "missing"}],
            "verification": None,
            "policy": None,
            "routing_decision": None,
            "follow_up_draft_count": 0,
        }

    def test_existing_follow_up_draft_stops_after_one_action(self):
        with (
            patch.object(
                NextActionPlanner, "case_snapshot", return_value=self.snapshot()
            ),
            patch.object(
                NextActionPlanner, "plan", return_value=plan("request_information")
            ),
            patch.object(
                ActionExecutor, "execute", return_value={"status": "already_available"}
            ) as execute,
        ):
            trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(12)

        execute.assert_called_once()
        self.assertEqual(execute.call_args.args, (12, plan("request_information")))
        self.assertEqual(execute.call_args.kwargs, {})
        self.assertEqual(stopping_reason, "follow_up_created")
        self.assertFalse(trace[0]["progress"])

    def test_duplicate_action_is_blocked(self):
        snapshots = [
            self.snapshot("pending_follow_up"),
            self.snapshot("pending_follow_up"),
            self.snapshot("pending_follow_up"),
            self.snapshot("pending_follow_up"),
            self.snapshot("pending_follow_up"),
            self.snapshot("pending_follow_up"),
        ]
        snapshots[2]["follow_up_draft_count"] = 1
        snapshots[3]["follow_up_draft_count"] = 1
        snapshots[4]["follow_up_draft_count"] = 1
        snapshots[5]["follow_up_draft_count"] = 1
        with (
            patch.object(
                NextActionPlanner, "case_snapshot", side_effect=snapshots
            ),
            patch.object(
                NextActionPlanner, "plan", return_value=plan(
                    "targeted_verification", ["ssm_roc_rob_number"]
                )
            ),
            patch.object(
                ActionExecutor, "execute",
                return_value={"status": "completed", "match_status": "matched"},
            ) as execute,
        ):
            trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(12)

        execute.assert_called_once()
        self.assertEqual(stopping_reason, "duplicate_action")
        self.assertEqual(trace[-1]["status"], "duplicate_action_blocked")

    def test_maximum_action_count_routes_unresolved_case_for_review(self):
        self.app.config["MAX_AGENTIC_ACTIONS"] = 2
        snapshots = [
            self.snapshot("verification_completed")
            for _ in range(7)
        ]
        for item, count in zip(snapshots, [0, 0, 1, 1, 1, 2, 2]):
            item["follow_up_draft_count"] = count
        plans = [
            plan("targeted_verification", ["ssm_roc_rob_number"]),
            plan("reprocess_stage", target_stage="verification"),
        ]
        with (
            patch.object(
                NextActionPlanner, "case_snapshot", side_effect=snapshots
            ),
            patch.object(NextActionPlanner, "plan", side_effect=plans) as planner,
            patch.object(
                ActionExecutor, "execute", return_value={"status": "completed"}
            ),
            patch.object(WorkflowOrchestrator, "_route_to_manual_review") as review,
        ):
            trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(12)

        self.assertEqual(planner.call_count, 2)
        self.assertEqual(len(trace), 2)
        self.assertEqual(stopping_reason, "maximum_actions_reached_manual_review")
        review.assert_called_once_with(12)

    def test_planner_and_executor_failures_route_safely(self):
        with (
            patch.object(NextActionPlanner, "case_snapshot", return_value=self.snapshot()),
            patch.object(
                NextActionPlanner, "plan",
                side_effect=NextActionPlannerError("invalid plan"),
            ),
            patch.object(WorkflowOrchestrator, "_route_to_manual_review") as review,
        ):
            trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(12)
        self.assertEqual(stopping_reason, "planner_failure")
        self.assertEqual(trace[0]["status"], "planner_failed")
        review.assert_called_once_with(12)

        with (
            patch.object(NextActionPlanner, "case_snapshot", return_value=self.snapshot()),
            patch.object(
                NextActionPlanner, "plan", return_value=plan("request_information")
            ),
            patch.object(
                ActionExecutor, "execute",
                side_effect=ActionExecutorError("draft generation failed"),
            ),
            patch.object(WorkflowOrchestrator, "_route_to_manual_review") as review,
        ):
            trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(12)
        self.assertEqual(stopping_reason, "execution_failure")
        self.assertEqual(trace[0]["status"], "execution_failed")
        review.assert_called_once_with(12)


class StageReprocessingTests(unittest.TestCase):
    def test_extraction_failure_routes_case_to_manual_review(self):
        app = Flask(__name__)
        case = SimpleNamespace(current_state="verification_completed")
        reprocessing_run = SimpleNamespace(
            id=17,
            run_status="started",
            new_case_state=None,
            completed_at=None,
        )
        case_query = SimpleNamespace(
            get_or_404=Mock(return_value=case),
            get=Mock(return_value=case),
        )
        with app.app_context():
            with (
                patch(
                    "app.orchestrators.reprocessing_orchestrator.VendorCase",
                    new=SimpleNamespace(query=case_query),
                ),
                patch(
                    "app.orchestrators.reprocessing_orchestrator.ReprocessingRun",
                    return_value=reprocessing_run,
                ),
                patch(
                    "app.orchestrators.reprocessing_orchestrator.ExtractionOrchestrator.run",
                    side_effect=RuntimeError("extraction failed"),
                ),
                patch("app.orchestrators.reprocessing_orchestrator.db.session.add"),
                patch("app.orchestrators.reprocessing_orchestrator.db.session.commit"),
                patch.object(ReprocessingOrchestrator, "_validate_prerequisites"),
                patch.object(
                    ReprocessingOrchestrator,
                    "_input_fingerprint",
                    return_value="test-fingerprint",
                ),
                patch.object(
                    ReprocessingOrchestrator,
                    "_result_fingerprint",
                    return_value="before",
                ),
                patch.object(
                    ReprocessingOrchestrator,
                    "_unresolved_issues",
                    return_value=[],
                ),
            ):
                result = ReprocessingOrchestrator.run_stage(12, "extraction")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["new_state"], "manual_review_required")
        self.assertEqual(case.current_state, "manual_review_required")
        self.assertEqual(reprocessing_run.run_status, "failed")


if __name__ == "__main__":
    unittest.main()
