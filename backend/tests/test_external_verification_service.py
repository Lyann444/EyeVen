import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.external_verification_service import ExternalVerificationService


class ExternalVerificationServiceTests(unittest.TestCase):
    def test_ssm_reports_common_syntax_without_claiming_registry_verification(self):
        result = ExternalVerificationService._verify_ssm(
            "201901045678 (1355008-X)"
        )

        self.assertEqual(result["status"], "format_match")
        self.assertIn("does not confirm that the registration exists", result["detail"])

    def test_unrecognized_ssm_syntax_is_not_reported_as_an_invalid_identifier(self):
        result = ExternalVerificationService._verify_ssm(
            "201901045678 (legacy number)"
        )

        self.assertEqual(result["status"], "format_unrecognized")
        self.assertIn("not evidence that the identifier is invalid", result["detail"])

    def test_tax_identifier_is_not_classified_without_authoritative_rules(self):
        result = ExternalVerificationService._verify_tax("SST1234567")

        self.assertEqual(result["status"], "not_assessed")
        self.assertIn("not validated", result["detail"])

    def test_whitespace_only_identifiers_are_treated_as_missing(self):
        self.assertEqual(
            ExternalVerificationService._verify_ssm("  ")["status"],
            "not_provided",
        )
        self.assertEqual(
            ExternalVerificationService._verify_tax("  ")["status"],
            "not_provided",
        )

    def test_internal_registry_match_skips_ssm_format_check_only(self):
        fields = [
            SimpleNamespace(field_name="ssm_roc_rob_number", value_text="invalid"),
            SimpleNamespace(
                field_name="tax_sst_gst_number",
                value_text="B16-1808-32001114",
            ),
            SimpleNamespace(
                field_name="iso_certification_available", value_text="no"
            ),
        ]
        with patch(
            "app.services.external_verification_service.EyeVenFormFieldValue"
        ) as model:
            model.query.filter_by.return_value.all.return_value = fields
            result = ExternalVerificationService.verify_identifiers(
                12, internal_registry_match={"match_status": "matched"}
            )

        self.assertEqual(result["checks"]["ssm_verification"]["status"], "skipped")
        self.assertEqual(
            result["checks"]["tax_verification"]["status"], "not_assessed"
        )
        self.assertEqual(
            result["checks"]["iso_verification"]["status"], "not_applicable"
        )
        self.assertFalse(result["external_registry_contacted"])
        self.assertEqual(result["overall_status"], "not_performed")
        self.assertEqual(
            result["verification_source"], "local_format_and_expiry_checks_only"
        )

    def test_unmatched_internal_registry_runs_ssm_syntax_check(self):
        fields = [
            SimpleNamespace(field_name="ssm_roc_rob_number", value_text="!"),
            SimpleNamespace(field_name="tax_sst_gst_number", value_text=""),
            SimpleNamespace(field_name="iso_certification_available", value_text="no"),
        ]
        with patch(
            "app.services.external_verification_service.EyeVenFormFieldValue"
        ) as model:
            model.query.filter_by.return_value.all.return_value = fields
            result = ExternalVerificationService.verify_identifiers(
                12, internal_registry_match={"match_status": "mismatched"}
            )

        self.assertEqual(
            result["checks"]["ssm_verification"]["status"], "format_unrecognized"
        )

    def test_parses_iso_expiry_in_long_month_format(self):
        parsed = ExternalVerificationService._parse_iso_expiry("09 January 2027")

        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2027-01-09")

    def test_current_iso_expiry_date_does_not_claim_certificate_is_verified(self):
        result = ExternalVerificationService._verify_iso(
            "yes",
            "MY-ISO9001-2024-8831",
            "09 January 2099",
        )

        self.assertEqual(result["status"], "expiry_date_current")
        self.assertIn("authenticity were not validated", result["detail"])

    def test_unknown_iso_claim_status_is_not_reported_as_verified(self):
        result = ExternalVerificationService._verify_iso("", "certificate-123", "")

        self.assertEqual(result["status"], "not_assessed")

    def test_expired_iso_date_is_reported_as_date_only(self):
        result = ExternalVerificationService._verify_iso(
            "yes",
            "certificate-123",
            "01 January 2000",
        )

        self.assertEqual(result["status"], "date_expired")
        self.assertIn("authenticity was not checked", result["detail"])


if __name__ == "__main__":
    unittest.main()
