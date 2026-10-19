import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator


class WorkflowOrchestrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
