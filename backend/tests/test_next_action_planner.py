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
        snapshot = {"fields": [{"field_name": "bank_account_number", "status": "missing"}]}
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
                side_effect=lambda _case_id, prior: {"prior_actions": prior},
            ),
            patch(
                "app.services.next_action_planner.GlmService.call_glm",
                return_value=response,
            ) as call_glm,
        ):
            result = NextActionPlanner.plan(12, [failed_extraction])

        self.assertEqual(result["target_stage"], "extraction")
        self.assertIn("Prior extraction stage returned failed", call_glm.call_args.kwargs["prompt"])

    def test_rejects_unknown_action_and_invalid_targets(self):
        for invalid_plan in (
            plan("run_shell"),
            plan("targeted_verification", ["bank_account_number"]),
            plan("reprocess_stage", target_stage="routing"),
            plan("finish", ["company_registered_number"]),
            plan("finish", ["company_registered_number", "company_registered_number"]),
        ):
            with self.subTest(invalid_plan=invalid_plan):
                with self.assertRaises(NextActionPlannerError):
                    NextActionPlanner.validate_plan(invalid_plan)

    def test_malformed_planner_json_is_rejected(self):
        with self.assertRaises(GlmMalformedOutputError):
            from app.services.glm_service import GlmService

            GlmService._parse_json_response("{invalid")

    def test_targeted_verification_uses_only_internal_registry(self):
        with patch(
            "app.services.action_executor.InternalRegistryService.match_vendor",
            return_value={"match_status": "matched", "matched_vendor_number": "V-10001"},
        ) as match_vendor:
            result = ActionExecutor.execute(
                12, plan("targeted_verification", ["ssm_roc_rob_number"])
            )

        match_vendor.assert_called_once_with(
            12, target_fields=["ssm_roc_rob_number"]
        )
        self.assertEqual(result["source"], "internal_demo_registry")
        self.assertFalse(result["external_verification_performed"])

    def test_executor_rejects_unknown_action_and_targets(self):
        with self.assertRaises(ActionExecutorError):
            ActionExecutor.execute(12, plan("unknown_action"))
        with self.assertRaises(ActionExecutorError):
            ActionExecutor.execute(
                12, plan("targeted_verification", ["bank_account_number"])
            )
        with self.assertRaises(ActionExecutorError):
            ActionExecutor.execute(
                12, plan("reprocess_stage", target_stage="routing")
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

    def test_no_progress_stops_after_one_action(self):
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
        self.assertEqual(stopping_reason, "no_meaningful_progress")
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
            ):
                result = ReprocessingOrchestrator.run_stage(12, "extraction")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["new_state"], "manual_review_required")
        self.assertEqual(case.current_state, "manual_review_required")
        self.assertEqual(reprocessing_run.run_status, "failed")


if __name__ == "__main__":
    unittest.main()
