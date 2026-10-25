import unittest
from types import SimpleNamespace

from app.services.context_assembler import ContextAssembler


def field(
    name,
    status,
    value="",
    normalized=None,
    source="procurement_form",
    source_file_id=None,
    excerpt=None,
    confidence=0.9,
):
    return SimpleNamespace(
        field_name=name,
        status=status,
        value_text=value,
        normalized_value_text=normalized,
        value_source=source,
        notes=None,
        source_file_id=source_file_id,
        source_excerpt=excerpt,
        confidence_score=confidence,
    )


class RoutingFieldEvidenceTests(unittest.TestCase):
    def test_reports_status_counts_and_unresolved_fields(self):
        fields = [
            field("company_registered_name", "matched_with_evidence", "Acme"),
            field(
                "bank_account_number",
                "conflicting",
                "123456789",
                source="supporting_document",
                source_file_id=7,
                excerpt="Bank account number: 123456789",
                confidence=0.55,
            ),
            field("contact_email", "missing"),
        ]

        result = ContextAssembler._routing_field_evidence(fields)

        self.assertEqual(result["field_status_counts"]["matched_with_evidence"], 1)
        self.assertEqual(result["field_status_counts"]["conflicting"], 1)
        self.assertEqual(result["field_status_counts"]["missing"], 1)
        self.assertEqual(result["unresolved_field_count"], 2)
        self.assertEqual(
            [item["field_name"] for item in result["unresolved_fields"]],
            ["bank_account_number", "contact_email"],
        )

        bank = result["unresolved_fields"][0]
        self.assertTrue(bank["value_present"])
        self.assertEqual(bank["value_source"], "supporting_document")
        self.assertTrue(bank["source_file_available"])
        self.assertTrue(bank["evidence_excerpt_available"])
        self.assertEqual(bank["confidence_score"], 0.55)

    def test_does_not_include_raw_vendor_values_or_source_excerpts(self):
        sensitive_value = "private-account-value"
        sensitive_excerpt = "private source document excerpt"
        fields = [
            field(
                "bank_account_number",
                "conflicting",
                sensitive_value,
                normalized=sensitive_value,
                excerpt=sensitive_excerpt,
            )
        ]

        result = ContextAssembler._routing_field_evidence(fields)
        serialized = str(result)

        self.assertNotIn(sensitive_value, serialized)
        self.assertNotIn(sensitive_excerpt, serialized)
        self.assertTrue(result["unresolved_fields"][0]["value_present"])
        self.assertTrue(result["unresolved_fields"][0]["evidence_excerpt_available"])


    def test_routing_prompt_formats_with_structured_evidence_context(self):
        from app.prompts.routing import ROUTING_PROMPT

        context = {
            "completeness_report": "{}",
            "verification_report": "{}",
            "policy_report": "{}",
            "field_evidence_report": '{"unresolved_field_count": 1}',
            "risk_band": "low",
            "recoverable_issues": 1,
            "blocking_issues": 0,
        }

        rendered = ROUTING_PROMPT.format(**context)

        self.assertIn("STRUCTURED FIELD EVIDENCE REPORT:", rendered)
        self.assertIn('"unresolved_field_count": 1', rendered)
        self.assertIn("deterministic safety gate remains authoritative", rendered.lower())

    def test_empty_fields_return_empty_summary(self):
        self.assertEqual(
            ContextAssembler._routing_field_evidence([]),
            {
                "field_status_counts": {},
                "unresolved_field_count": 0,
                "pic_supplied_fields": [],
                "vendor_response_received_fields": [],
                "unresolved_fields": [],
            },
        )

    def test_optional_unresolved_fields_are_omitted_from_routing_evidence(self):
        result = ContextAssembler._routing_field_evidence([
            field("business_type", "missing"),
            field("outsourcing_details", "conflicting", "hidden"),
            field("contact_mobile_number", "missing"),
        ])

        self.assertEqual(result["unresolved_field_count"], 1)
        self.assertEqual(
            result["unresolved_fields"][0]["field_name"],
            "contact_mobile_number",
        )

    def test_vendor_response_is_reported_without_remaining_unresolved(self):
        response = field("latest_annual_revenue_range", "low_confidence", "RM1M-5M")
        response.notes = (
            "Vendor response received and transcribed by PIC."
        )

        result = ContextAssembler._routing_field_evidence([response])

        self.assertEqual(result["unresolved_field_count"], 0)
        self.assertEqual(
            result["vendor_response_received_fields"],
            ["latest_annual_revenue_range"],
        )

    def test_manual_revenue_correction_is_not_reported_as_follow_up(self):
        revenue = field("latest_annual_revenue_range", "low_confidence", "50-100k")
        revenue.notes = "PIC manual correction; not independently verified."

        result = ContextAssembler._routing_field_evidence([revenue])

        self.assertEqual(result["unresolved_field_count"], 0)
        self.assertEqual(
            result["pic_supplied_fields"],
            ["latest_annual_revenue_range"],
        )


if __name__ == "__main__":
    unittest.main()
