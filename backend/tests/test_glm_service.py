import json
import unittest
from unittest.mock import patch

from flask import Flask

from app.services.glm_service import GlmService
from app.validators.agent_schemas import POLICY_EVALUATION_SCHEMA, VERIFICATION_SCHEMA


class GlmServiceTests(unittest.TestCase):
    def test_normalizes_string_verification_mismatches(self):
        response = {
            "legitimacy_status": "verified",
            "verified_identity_summary": "Vendor identity verified.",
            "critical_conflicts": [],
            "missing_critical_fields": [],
            "critical_mismatches": ["company_registered_number"],
            "summary": "No material concerns found.",
            "confidence": 0.95,
            "recommended_next_step": "Proceed.",
        }
        app = Flask(__name__)
        app.config["GLM_MODEL"] = "test-model"

        with app.app_context():
            with patch.object(
                GlmService, "_make_request", return_value=json.dumps(response)
            ):
                result = GlmService.call_glm(
                    prompt="Verify vendor.",
                    response_schema=VERIFICATION_SCHEMA,
                )

        self.assertEqual(
            result["critical_mismatches"],
            [{
                "field_name": "company_registered_number",
                "description": "Mismatch reported without additional details.",
            }],
        )

    def test_preserves_object_verification_mismatches(self):
        mismatch = {
            "field_name": "company_registered_number",
            "description": "The submitted values differ.",
        }
        result = GlmService._normalize_verification_mismatches({
            "critical_mismatches": [mismatch],
        })

        self.assertIs(result["critical_mismatches"][0], mismatch)

    def test_accepts_recoverable_policy_risk_flag_severity(self):
        response = {
            "risk_band": "medium",
            "risk_flags": [{
                "flag_type": "registration_gap",
                "description": "Registration details need follow-up.",
                "severity": "recoverable",
            }],
            "blocking_issues": [],
            "recoverable_issues": ["Provide registration details."],
            "blocking_issue_count": 0,
            "recoverable_issue_count": 1,
            "policy_summary": "The case has a recoverable registration gap.",
            "policy_confidence": 0.7,
            "recommended_next_step": "Request clarification.",
        }
        app = Flask(__name__)
        app.config["GLM_MODEL"] = "test-model"

        with app.app_context():
            with patch.object(
                GlmService, "_make_request", return_value=json.dumps(response)
            ):
                result = GlmService.call_glm(
                    prompt="Evaluate vendor policy.",
                    response_schema=POLICY_EVALUATION_SCHEMA,
                )

        self.assertEqual(result["risk_flags"][0]["severity"], "recoverable")


if __name__ == "__main__":
    unittest.main()
