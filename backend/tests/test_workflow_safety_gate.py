import unittest
from unittest.mock import patch

from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator


class WorkflowSafetyGateTests(unittest.TestCase):
    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_blocked_approval_is_overridden_before_persistence(self, evaluate):
        evaluate.return_value = {
            "allowed": False,
            "suggested_decision": "follow_up_required",
            "reasons": ["unresolved_required_information"],
            "recoverable_fields": ["bank_account_number"],
            "review_blockers": [],
        }

        decision, reasoning, action, gate_result = (
            WorkflowOrchestrator._apply_safety_gate(
                42,
                "complete_verified",
                "Vendor appears eligible.",
                "Approve vendor",
                0.95,
            )
        )

        self.assertEqual(decision, "follow_up_required")
        self.assertIn("Safety gate blocked automatic approval", reasoning)
        self.assertIn("unresolved_required_information", reasoning)
        self.assertEqual(action, "Request missing information")
        self.assertFalse(gate_result["allowed"])
        evaluate.assert_called_once_with(42)

    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_allowed_approval_remains_approved(self, evaluate):
        evaluate.return_value = {
            "allowed": True,
            "suggested_decision": "complete_verified",
            "reasons": [],
            "recoverable_fields": [],
            "review_blockers": [],
        }

        decision, reasoning, action, gate_result = (
            WorkflowOrchestrator._apply_safety_gate(
                42,
                "complete_verified",
                "All checks passed.",
                "Approve vendor",
                0.95,
            )
        )

        self.assertEqual(decision, "complete_verified")
        self.assertEqual(reasoning, "All checks passed.")
        self.assertEqual(action, "Approve vendor")
        self.assertTrue(gate_result["allowed"])
        evaluate.assert_called_once_with(42)


    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_low_confidence_approval_is_forced_to_manual_review(self, evaluate):
        decision, reasoning, action, gate_result = (
            WorkflowOrchestrator._apply_safety_gate(
                42,
                "complete_verified",
                "Vendor appears eligible.",
                "Approve vendor",
                0.62,
            )
        )

        self.assertEqual(decision, "manual_review_required")
        self.assertIn("approval_confidence_below_threshold", reasoning)
        self.assertEqual(action, "Escalate for human review")
        self.assertFalse(gate_result["allowed"])
        evaluate.assert_not_called()

    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_missing_confidence_is_forced_to_manual_review(self, evaluate):
        decision, _, action, gate_result = (
            WorkflowOrchestrator._apply_safety_gate(
                42,
                "complete_verified",
                "Vendor appears eligible.",
                "Approve vendor",
                None,
            )
        )

        self.assertEqual(decision, "manual_review_required")
        self.assertEqual(action, "Escalate for human review")
        self.assertIn("approval_confidence_missing_or_invalid", gate_result["reasons"])
        evaluate.assert_not_called()


    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_non_finite_confidence_is_forced_to_manual_review(self, evaluate):
        for invalid_confidence in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(confidence=invalid_confidence):
                decision, _, action, gate_result = (
                    WorkflowOrchestrator._apply_safety_gate(
                        42,
                        "complete_verified",
                        "Vendor appears eligible.",
                        "Approve vendor",
                        invalid_confidence,
                    )
                )

                self.assertEqual(decision, "manual_review_required")
                self.assertEqual(action, "Escalate for human review")
                self.assertIn(
                    "approval_confidence_missing_or_invalid",
                    gate_result["reasons"],
                )
        evaluate.assert_not_called()

    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_string_confidence_is_forced_to_manual_review(self, evaluate):
        decision, _, action, gate_result = (
            WorkflowOrchestrator._apply_safety_gate(
                42,
                "complete_verified",
                "Vendor appears eligible.",
                "Approve vendor",
                "0.99",
            )
        )

        self.assertEqual(decision, "manual_review_required")
        self.assertEqual(action, "Escalate for human review")
        self.assertIn(
            "approval_confidence_missing_or_invalid",
            gate_result["reasons"],
        )
        evaluate.assert_not_called()

    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_non_approval_decision_does_not_call_gate(self, evaluate):
        result = WorkflowOrchestrator._apply_safety_gate(
            42,
            "manual_review_required",
            "Evidence is inconsistent.",
            "Escalate for human review",
        )

        self.assertEqual(
            result,
            (
                "manual_review_required",
                "Evidence is inconsistent.",
                "Escalate for human review",
                None,
            ),
        )
        evaluate.assert_not_called()

    @patch("app.orchestrators.workflow_orchestrator.SafetyGate.evaluate")
    def test_blocked_approval_with_high_risk_routes_to_review_action(self, evaluate):
        evaluate.return_value = {
            "allowed": False,
            "suggested_decision": "flagged_high_risk",
            "reasons": ["policy_risk_not_low:high"],
            "recoverable_fields": [],
            "review_blockers": [],
        }

        decision, reasoning, action, gate_result = (
            WorkflowOrchestrator._apply_safety_gate(
                42,
                "complete_verified",
                "Vendor appears eligible.",
                "Approve vendor",
                0.95,
            )
        )

        self.assertEqual(decision, "flagged_high_risk")
        self.assertEqual(action, "Escalate to high-risk procurement review")
        self.assertIn("policy_risk_not_low:high", reasoning)
        self.assertFalse(gate_result["allowed"])


if __name__ == "__main__":
    unittest.main()
