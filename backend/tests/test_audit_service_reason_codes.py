import unittest

from app.services.audit_service import AuditService


class AuditStructuredDecisionTests(unittest.TestCase):
    def test_parses_reason_code_list(self):
        self.assertEqual(
            AuditService._parse_json_list(
                '["approval_confidence_missing_or_invalid", "policy_result_missing"]'
            ),
            [
                "approval_confidence_missing_or_invalid",
                "policy_result_missing",
            ],
        )

    def test_invalid_or_non_list_reason_codes_return_empty_list(self):
        self.assertEqual(AuditService._parse_json_list("not-json"), [])
        self.assertEqual(AuditService._parse_json_list('{"reason": "x"}'), [])
        self.assertEqual(AuditService._parse_json_list(None), [])

    def test_parses_safety_gate_result(self):
        self.assertEqual(
            AuditService._parse_json_object(
                '{"allowed": false, "reasons": ["policy_result_missing"]}'
            ),
            {"allowed": False, "reasons": ["policy_result_missing"]},
        )

    def test_invalid_or_non_object_gate_result_returns_none(self):
        self.assertIsNone(AuditService._parse_json_object("not-json"))
        self.assertIsNone(AuditService._parse_json_object('["not", "an object"]'))
        self.assertIsNone(AuditService._parse_json_object(None))


if __name__ == "__main__":
    unittest.main()
