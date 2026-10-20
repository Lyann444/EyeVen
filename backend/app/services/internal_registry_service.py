import json
import logging
import re

from app.extensions import db
from app.models import EyeVenFormFieldValue, VendorCase

logger = logging.getLogger(__name__)

MOCK_REGISTRY = [
    {
        "vendor_number": "V-10001",
        "company_registered_name": "Satria Teknologi Sdn Bhd",
        "company_registered_number": "202001012345",
        "ssm_roc_rob_number": "202001012345",
        "tax_sst_gst_number": "GST1234567",
        "company_email": "info@satriateknologi.my",
        "contact_person_name": "Ahmad Razak",
        "business_type": "Sdn Bhd",
    },
    {
        "vendor_number": "V-10002",
        "company_registered_name": "Nusantara Solutions Sdn Bhd",
        "company_registered_number": "201903004567",
        "ssm_roc_rob_number": "201903004567",
        "tax_sst_gst_number": "SST9876543",
        "company_email": "admin@nusantarasolutions.com",
        "contact_person_name": "Lim Wei Chong",
        "business_type": "Sdn Bhd",
    },
    {
        "vendor_number": "V-20021",
        "company_registered_name": "Apex Office Supplies Sdn. Bhd.",
        "company_registered_number": "201901045678",
        "ssm_roc_rob_number": "201901045678",
        "tax_sst_gst_number": "B16-1808-32001114",
        "company_email": "sales@apexoffice.com.my",
        "contact_person_name": "Nur Aisyah Rahman",
        "business_type": "Sdn Bhd",
    },
    {
        "vendor_number": "V-10003",
        "company_registered_name": "Petra Dynamics Sdn Bhd",
        "company_registered_number": "201506007890",
        "ssm_roc_rob_number": "201506007890",
        "tax_sst_gst_number": "SST5551234",
        "company_email": "contact@petradynamics.my",
        "contact_person_name": "Siti Aminah",
        "business_type": "Sdn Bhd",
    },
]


class InternalRegistryService:
    @staticmethod
    def match_vendor(case_id, target_fields=None):
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_map = {f.field_name: (f.value_text or "").strip().lower() for f in fields}

        checks = [
            ("ssm_roc_rob_number", "ssm_roc_rob_number"),
            ("company_registered_name", "company_registered_name"),
            ("company_registered_number", "company_registered_number"),
        ]
        if target_fields is not None:
            supported_fields = {
                "company_registered_number",
                "ssm_roc_rob_number",
            }
            if not target_fields or not set(target_fields).issubset(supported_fields):
                raise ValueError("Unsupported internal registry target fields")
            checks = [item for item in checks if item[0] in target_fields]

        submitted_ssm = field_map.get("ssm_roc_rob_number", "")
        submitted_name = field_map.get("company_registered_name", "")
        submitted_reg_no = field_map.get("company_registered_number", "")

        if not submitted_ssm and not submitted_name and not submitted_reg_no:
            return {
                "match_status": "no_match",
                "reason": "No identifying fields available to match against registry",
                "matched_record": None,
                "field_comparisons": [],
            }

        best_match = None
        best_score = 0
        comparisons = []
        match_threshold = min(2, len(checks))

        for record in MOCK_REGISTRY:
            score = 0
            max_score = 0
            field_results = []

            for field_name, registry_key in checks:
                registry_value = record[registry_key]
                submitted = field_map.get(field_name, "")
                max_score += 1

                if not submitted:
                    field_results.append({
                        "field_name": field_name,
                        "submitted_value": None,
                        "registry_value": registry_value,
                        "match": "not_provided",
                    })
                elif InternalRegistryService._normalize_field_value(field_name, submitted) == InternalRegistryService._normalize_field_value(registry_key, registry_value):
                    score += 1
                    field_results.append({
                        "field_name": field_name,
                        "submitted_value": field_map.get(field_name),
                        "registry_value": registry_value,
                        "match": "exact",
                    })
                elif InternalRegistryService._fuzzy_match(submitted, registry_value.lower()):
                    score += 0.5
                    field_results.append({
                        "field_name": field_name,
                        "submitted_value": field_map.get(field_name),
                        "registry_value": registry_value,
                        "match": "partial",
                    })
                else:
                    field_results.append({
                        "field_name": field_name,
                        "submitted_value": field_map.get(field_name),
                        "registry_value": registry_value,
                        "match": "mismatch",
                    })

            comparisons.append({
                "vendor_number": record["vendor_number"],
                "company_name": record["company_registered_name"],
                "score": score,
                "max_score": max_score,
                "field_results": field_results,
            })

            if score > best_score:
                best_score = score
                best_match = record

        if best_score >= match_threshold:
            match_status = "matched"
        elif best_score >= 1 or (
            target_fields is not None
            and any(field_map.get(field_name) for field_name in target_fields)
        ):
            match_status = "mismatched"
        else:
            match_status = "no_match"

        result = {
            "match_status": match_status,
            "matched_vendor_number": best_match["vendor_number"] if best_match else None,
            "match_score": best_score,
            "field_comparisons": comparisons,
        }

        if best_match and match_status == "matched":
            result["registry_record"] = best_match
        elif best_match and match_status == "mismatched":
            result["registry_record"] = best_match
            result["mismatch_details"] = [
                c for c in comparisons
                if c["vendor_number"] == best_match["vendor_number"]
                and any(f["match"] == "mismatch" for f in c["field_results"])
            ]
        else:
            result["registry_record"] = None

        return result

    @staticmethod
    def _normalize_field_value(field_name, value):
        """Normalize registration identifiers so suffixes/punctuation do not cause false mismatches."""
        normalized = (value or "").strip().lower()
        if field_name in {"ssm_roc_rob_number", "company_registered_number"}:
            # Malaysian registration documents may include a legacy number in parentheses.
            match = re.search(r"\b\d{12}\b", normalized)
            if match:
                return match.group(0)
        return re.sub(r"\s+", " ", normalized)

    @staticmethod
    def _fuzzy_match(a, b):
        if not a or not b:
            return False
        a_words = set(a.split())
        b_words = set(b.split())
        if not a_words or not b_words:
            return False
        overlap = a_words & b_words
        return len(overlap) / max(len(a_words), len(b_words)) >= 0.5
