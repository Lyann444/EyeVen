import unittest
from types import SimpleNamespace

from app.services.safety_gate import SafetyGate


def verification(status="verified", severity="minor", run_status="completed"):
    return SimpleNamespace(
        legitimacy_status=status,
        mismatch_severity=severity,
        run_status=run_status,
    )


def policy(risk_band="low", blocking_issue_count=0):
    return SimpleNamespace(
        risk_band=risk_band,
        blocking_issue_count=blocking_issue_count,
    )


def field(name="registration_number", status="matched_with_evidence"):
    return SimpleNamespace(field_name=name, status=status)


class SafetyGateTests(unittest.TestCase):
    def test_all_checks_pass_allows_approval(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(), [field()]
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["suggested_decision"], "complete_verified")
        self.assertEqual(result["reasons"], [])

    def test_missing_information_requires_follow_up(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(), [field(status="missing")]
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "follow_up_required")
        self.assertIn("unresolved_required_information", result["reasons"])

    def test_conflicting_evidence_requires_manual_review(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(), [field(status="conflicting")]
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")

    def test_high_risk_case_cannot_be_auto_approved(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(risk_band="high"), [field()]
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "flagged_high_risk")

    def test_missing_policy_result_fails_closed(self):
        result = SafetyGate.evaluate_results(
            verification(), None, [field()]
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")
        self.assertIn("policy_result_missing", result["reasons"])

    def test_blocking_policy_issue_prevents_approval(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(blocking_issue_count=1), [field()]
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")

    def test_moderate_mismatch_prevents_approval(self):
        result = SafetyGate.evaluate_results(
            verification(severity="moderate"), policy(), [field()]
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")


if __name__ == "__main__":
    unittest.main()
