import unittest

from app.services.glm_service import GlmService
from app.validators.agent_schemas import VERIFICATION_SCHEMA, ROUTING_SCHEMA


class GlmMismatchNormalizationTests(unittest.TestCase):
    def test_bare_field_name_is_structured_without_inventing_evidence(self):
        payload = {
            "critical_mismatches": ["company_registered_number"],
            "minor_mismatches": [],
            "moderate_mismatches": [],
        }

        result = GlmService._normalize_mismatch_arrays(payload, VERIFICATION_SCHEMA)

        self.assertEqual(result["critical_mismatches"][0]["field_name"], "company_registered_number")
        self.assertIn("requires manual review", result["critical_mismatches"][0]["description"])

    def test_existing_objects_are_preserved(self):
        item = {"field_name": "company_name", "description": "Name differs"}
        payload = {"critical_mismatches": [item]}

        result = GlmService._normalize_mismatch_arrays(payload, VERIFICATION_SCHEMA)

        self.assertEqual(result["critical_mismatches"], [item])

    def test_non_verification_schema_is_not_modified(self):
        payload = {"critical_mismatches": ["company_registered_number"]}

        result = GlmService._normalize_mismatch_arrays(payload, ROUTING_SCHEMA)

        self.assertEqual(result, payload)


if __name__ == "__main__":
    unittest.main()
