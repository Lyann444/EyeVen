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
    fields.extend([
        make_field("business_type", status="missing", value=""),
        make_field("authorized_signature_present", status="missing", value=""),
        make_field("company_chop_present", status="missing", value=""),
        make_field("outsourcing_details", status="missing", value=""),
        make_field("legal_disputes_past_5_years", value=""),
        make_field("outsource_any_part_of_services", value=""),
    ])
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

    def test_manual_correction_without_independent_evidence_blocks_approval(self):
        fields = complete_fields()
        contact_email = next(
            item for item in fields if item.field_name == "contact_email"
        )
        contact_email.status = "low_confidence"
        contact_email.notes = (
            "PIC manual correction; not independently verified. Previous extracted value: None."
        )

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")
        self.assertIn(
            "contact_email:manual_correction_not_independently_verified",
            result["review_blockers"],
        )

    def test_manual_correction_on_out_of_scope_field_does_not_block_approval(self):
        fields = complete_fields()
        declaration_date = make_field(
            "declaration_date", status="low_confidence", value="2026-10-20"
        )
        declaration_date.notes = (
            "PIC manual correction; not independently verified."
        )
        outsourcing_details = make_field(
            "outsourcing_details", status="conflicting", value="Vendor A"
        )
        fields.extend([declaration_date, outsourcing_details])

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])
        self.assertEqual(result["review_blockers"], [])

    def test_manual_correction_on_unclaimed_iso_details_is_out_of_scope(self):
        fields = complete_fields()
        iso_number = make_field(
            "iso_certification_number", status="low_confidence", value="ISO-123"
        )
        iso_number.notes = "PIC manual correction; not independently verified."
        fields.append(iso_number)

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])
        self.assertEqual(result["review_blockers"], [])

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


    def test_critical_identity_field_requires_corroborating_evidence(self):
        fields = complete_fields()
        company_number = next(
            item for item in fields
            if item.field_name == "company_registered_number"
        )
        company_number.status = "provided_in_form"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "manual_review_required")
        self.assertTrue(
            any(
                item.startswith("company_registered_number:evidence_not_corroborated")
                for item in result["review_blockers"]
            )
        )

    def test_exact_registry_identifier_match_satisfies_its_evidence_requirement(self):
        fields = complete_fields()
        company_number = next(
            item for item in fields
            if item.field_name == "company_registered_number"
        )
        company_number.status = "provided_in_form"
        registry_match = {
            "match_status": "matched",
            "matched_vendor_number": "V-10004",
            "field_comparisons": [
                {
                    "vendor_number": "V-10004",
                    "field_results": [
                        {
                            "field_name": "company_registered_number",
                            "match": "exact",
                        }
                    ],
                }
            ],
        }

        result = SafetyGate.evaluate_results(
            verification(), policy(), fields, registry_match=registry_match
        )

        self.assertTrue(result["allowed"])
        self.assertNotIn(
            "company_registered_number:evidence_not_corroborated:provided_in_form",
            result["review_blockers"],
        )

    def test_registry_partial_identifier_match_does_not_satisfy_evidence(self):
        fields = complete_fields()
        company_number = next(
            item for item in fields
            if item.field_name == "company_registered_number"
        )
        company_number.status = "provided_in_form"
        registry_match = {
            "match_status": "matched",
            "matched_vendor_number": "V-10004",
            "field_comparisons": [
                {
                    "vendor_number": "V-10004",
                    "field_results": [
                        {
                            "field_name": "company_registered_number",
                            "match": "partial",
                        }
                    ],
                }
            ],
        }

        result = SafetyGate.evaluate_results(
            verification(), policy(), fields, registry_match=registry_match
        )

        self.assertFalse(result["allowed"])
        self.assertTrue(
            any(
                item.startswith("company_registered_number:evidence_not_corroborated")
                for item in result["review_blockers"]
            )
        )

    def test_internal_registry_match_satisfies_bank_field_evidence_requirement(self):
        fields = complete_fields()
        for field in fields:
            if field.field_name in SafetyGate.BANK_FIELDS:
                field.status = "provided_in_form"
        registry_match = {
            "match_status": "matched",
            "matched_vendor_number": "V-10004",
            "field_comparisons": [],
        }

        result = SafetyGate.evaluate_results(
            verification(), policy(), fields, registry_match=registry_match
        )

        self.assertTrue(result["allowed"])
        self.assertFalse(
            any(
                blocker.split(":", 1)[0] in SafetyGate.BANK_FIELDS
                for blocker in result["review_blockers"]
            )
        )

    def test_bank_fields_still_need_corroboration_without_registry_match(self):
        fields = complete_fields()
        for field in fields:
            if field.field_name in SafetyGate.BANK_FIELDS:
                field.status = "provided_in_form"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertTrue(
            all(
                any(
                    blocker.startswith(
                        field_name + ":evidence_not_corroborated"
                    )
                    for blocker in result["review_blockers"]
                )
                for field_name in SafetyGate.BANK_FIELDS
            )
        )

    def test_supporting_document_status_satisfies_evidence_requirement(self):
        fields = complete_fields()
        bank_account = next(
            item for item in fields if item.field_name == "bank_account_number"
        )
        bank_account.status = "filled_from_supporting_docs"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])
        self.assertEqual(result["suggested_decision"], "complete_verified")


    def test_blank_signature_and_chop_are_optional(self):
        fields = complete_fields()

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_missing_important_website_requires_follow_up(self):
        fields = complete_fields()
        fields.append(make_field("company_website", status="missing", value=""))

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["suggested_decision"], "follow_up_required")
        self.assertIn("company_website", result["recoverable_fields"])

    def test_re_registration_vendor_number_is_outside_follow_up_scope(self):
        fields = complete_fields()
        fields.append(
            make_field("current_vendor_number", status="missing", value="")
        )

        result = SafetyGate.evaluate_results(
            verification(), policy(), fields, registration_type="re_registration"
        )

        self.assertTrue(result["allowed"])

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


    def test_business_type_and_other_details_are_outside_follow_up_scope(self):
        fields = complete_fields()
        business_type = next(
            item for item in fields if item.field_name == "business_type"
        )
        business_type.value_text = "other"
        business_type.normalized_value_text = "other"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_legal_dispute_details_are_outside_follow_up_scope(self):
        fields = complete_fields()
        disputes = next(
            item for item in fields
            if item.field_name == "legal_disputes_past_5_years"
        )
        disputes.value_text = "yes"
        disputes.normalized_value_text = "yes"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_outsourcing_details_are_outside_follow_up_scope_even_when_yes(self):
        fields = complete_fields()
        outsourcing = next(
            item for item in fields
            if item.field_name == "outsource_any_part_of_services"
        )
        outsourcing.value_text = "yes"
        outsourcing.normalized_value_text = "yes"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_unknown_registration_type_is_optional(self):
        result = SafetyGate.evaluate_results(
            verification(),
            policy(),
            complete_fields(),
            registration_type=None,
        )

        self.assertTrue(result["allowed"])

    def test_business_type_may_be_blank(self):
        fields = complete_fields()
        business_type = next(
            item for item in fields if item.field_name == "business_type"
        )
        business_type.value_text = ""
        business_type.normalized_value_text = ""
        business_type.status = "missing"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_missing_outsourcing_details_are_optional_when_answer_is_no(self):
        fields = complete_fields()
        outsourcing = next(
            item for item in fields
            if item.field_name == "outsource_any_part_of_services"
        )
        outsourcing.value_text = "no"
        outsourcing.normalized_value_text = "no"

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_unresolved_status_on_non_follow_up_field_does_not_block(self):
        fields = complete_fields()
        optional = make_field(
            "business_type", status="conflicting", value="Private Limited"
        )
        fields.append(optional)

        result = SafetyGate.evaluate_results(verification(), policy(), fields)

        self.assertTrue(result["allowed"])

    def test_iso_details_are_not_followed_up_when_iso_not_claimed(self):
        fields = [
            make_field("iso_certification_available", value="no"),
            make_field("iso_certification_number", status="missing", value=""),
            make_field("iso_certification_expiry_date", status="missing", value=""),
        ]
        values = {
            item.field_name: SafetyGate._field_value(item)
            for item in fields
        }

        self.assertFalse(SafetyGate.is_follow_up_needed(fields[1], values))
        self.assertFalse(SafetyGate.is_follow_up_needed(fields[2], values))

    def test_follow_up_scope_includes_requested_fields_but_not_business_type(self):
        important = make_field("contact_mobile_number", status="missing", value="")
        optional = make_field("business_type", status="missing", value="")

        self.assertTrue(SafetyGate.is_follow_up_needed(important))
        self.assertFalse(SafetyGate.is_follow_up_needed(optional))


if __name__ == "__main__":
    unittest.main()
