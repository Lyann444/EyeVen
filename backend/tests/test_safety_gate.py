import unittest
from types import SimpleNamespace

from app.services.safety_gate import SafetyGate


def verification(status="verified", severity="minor", run_status="completed"):
    return SimpleNamespace(
        legitimacy_status=status,
        mismatch_severity=severity,
        run_status=run_status,
    )


def policy(risk_band="low", blocking_issue_count=0, risk_flags="[]"):
    return SimpleNamespace(
        risk_band=risk_band,
        blocking_issue_count=blocking_issue_count,
        risk_flags=risk_flags,
    )


def make_field(name, status="matched_with_evidence", value="test-value"):
    return SimpleNamespace(
        field_name=name,
        status=status,
        value_text=value,
        normalized_value_text=value,
    )


def complete_fields():
    """Build a minimal case fixture satisfying the proposed demo policy."""
    fields = [
        make_field(name)
        for name in SafetyGate.REQUIRED_FIELDS
    ]
    # These declarations control whether extra conditional fields are required.
    overrides = {
        "business_type": "services",
        "legal_disputes_past_5_years": "no",
        "outsource_any_part_of_services": "no",
    }
    for name, value in overrides.items():
        existing = next(item for item in fields if item.field_name == name)
        existing.value_text = value
        existing.normalized_value_text = value
    return fields


class SafetyGateTests(unittest.TestCase):
    def test_all_checks_pass_allows_approval(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(), complete_fields()
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["suggested_decision"], "complete_verified")
        self.assertEqual(result["reasons"], [])

    def test_missing_required_information_requires_follow_up(self):
        fields = complete_fields()
        bank_account = next(
            item for item in fields if item.field_name == "bank_account_number"
        )
        bank_account.status = "missing"
        bank_account.value_text = None
        bank_account.normalized_value_text = None

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "follow_up_required")
        self.assertIn("bank_account_number", result["recoverable_fields"])

    def test_conflicting_evidence_requires_manual_review(self):
        fields = complete_fields()
        fields.append(make_field("company_website", status="conflicting"))

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")

    def test_high_risk_case_cannot_be_auto_approved(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(risk_band="high"), complete_fields()
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "flagged_high_risk")

    def test_missing_policy_result_fails_closed(self):
        result = SafetyGate.evaluate_results(
            verification(), None, complete_fields()
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")
        self.assertIn("policy_result_missing", result["reasons"])

    def test_blocking_policy_issue_prevents_approval(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(blocking_issue_count=1), complete_fields()
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")

    def test_moderate_mismatch_prevents_approval(self):
        result = SafetyGate.evaluate_results(
            verification(severity="moderate"), policy(), complete_fields()
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")

    def test_missing_mismatch_severity_fails_closed(self):
        result = SafetyGate.evaluate_results(
            verification(severity=None), policy(), complete_fields()
        )

        self.assertFalse(result["allowed"])
        self.assertIn("mismatch_severity_missing_or_unknown", result["reasons"])

    def test_blocking_flag_prevents_approval_even_if_count_is_zero(self):
        result = SafetyGate.evaluate_results(
            verification(),
            policy(risk_flags='[{"severity":"blocking","flag_type":"identity_verification_gap"}]'),
            complete_fields(),
        )

        self.assertFalse(result["allowed"])
        self.assertIn("blocking_risk_flag_present", result["reasons"])

    def test_malformed_risk_flags_fail_closed(self):
        result = SafetyGate.evaluate_results(
            verification(), policy(risk_flags="not-json"), complete_fields()
        )

        self.assertFalse(result["allowed"])
        self.assertIn("policy_risk_flags_malformed", result["reasons"])

    def test_missing_optional_field_does_not_block_approval(self):
        fields = complete_fields()
        fields.append(make_field("company_website", status="missing", value=""))

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_re_registration_requires_current_vendor_number(self):
        fields = complete_fields()
        fields.append(
            make_field("current_vendor_number", status="missing", value="")
        )

        result = SafetyGate.evaluate_results(
            verification(), policy(), fields, registration_type="re_registration"
        )

        self.assertFalse(result["allowed"])
        self.assertIn("current_vendor_number", result["recoverable_fields"])

    def test_iso_details_required_when_certification_is_available(self):
        fields = complete_fields()
        fields.extend([
            make_field("iso_certification_available", value="yes"),
            make_field("iso_certification_number", status="missing", value=""),
            make_field("iso_certification_expiry_date", status="missing", value=""),
        ])

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertIn("iso_certification_number", result["recoverable_fields"])
        self.assertIn("iso_certification_expiry_date", result["recoverable_fields"])

    def test_iso_details_not_required_when_certification_is_unavailable(self):
        fields = complete_fields()
        fields.extend([
            make_field("iso_certification_available", value="no"),
            make_field("iso_certification_number", status="missing", value=""),
            make_field("iso_certification_expiry_date", status="missing", value=""),
        ])

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])


    def test_other_business_type_requires_details(self):
        fields = complete_fields()
        business_type = next(
            item for item in fields if item.field_name == "business_type"
        )
        business_type.value_text = "other"
        business_type.normalized_value_text = "other"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertIn("business_type_other", result["recoverable_fields"])

    def test_dispute_details_required_when_disputes_declared(self):
        fields = complete_fields()
        disputes = next(
            item for item in fields
            if item.field_name == "legal_disputes_past_5_years"
        )
        disputes.value_text = "yes"
        disputes.normalized_value_text = "yes"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertIn("legal_disputes_details", result["recoverable_fields"])

    def test_outsourcing_details_required_when_outsourcing_declared(self):
        fields = complete_fields()
        outsourcing = next(
            item for item in fields
            if item.field_name == "outsource_any_part_of_services"
        )
        outsourcing.value_text = "yes"
        outsourcing.normalized_value_text = "yes"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertIn("outsourcing_details", result["recoverable_fields"])

    def test_unknown_registration_type_fails_closed(self):
        result = SafetyGate.evaluate_results(
            verification(),
            policy(),
            complete_fields(),
            registration_type=None,
        )

        self.assertFalse(result["allowed"])
        self.assertIn("registration_type", result["recoverable_fields"])


if __name__ == "__main__":
    unittest.main()
