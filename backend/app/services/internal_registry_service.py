import logging

from app.extensions import db
from app.models import EyeVenFormFieldValue, VendorRegistry

logger = logging.getLogger(__name__)


class InternalRegistryService:
    @staticmethod
    def match_vendor(case_id):
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_map = {f.field_name: (f.value_text or "").strip().lower() for f in fields}

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

        # --- Database lookup by SSM number (primary identifier) ---
        db_records = []
        if submitted_ssm:
            db_records = VendorRegistry.query.filter(
                db.func.lower(VendorRegistry.ssm_roc_rob_number) == submitted_ssm
            ).all()

        # Fallback: search by registered name if no SSM match
        if not db_records and submitted_name:
            db_records = VendorRegistry.query.filter(
                db.func.lower(VendorRegistry.company_registered_name).contains(submitted_name)
            ).all()

        # Fallback: search by company registered number
        if not db_records and submitted_reg_no:
            db_records = VendorRegistry.query.filter(
                db.func.lower(VendorRegistry.company_registered_number) == submitted_reg_no
            ).all()

        if not db_records:
            return {
                "match_status": "no_match",
                "reason": "No matching record found in internal vendor registry",
                "matched_vendor_number": None,
                "match_score": 0,
                "registry_record": None,
                "field_comparisons": [],
            }

        # --- Risk flag check: immediate rejection if any match is high-risk ---
        for record in db_records:
            if record.is_past_high_risk:
                logger.warning(
                    "Vendor %s flagged as high-risk (reason: %s) for case %s",
                    record.vendor_number,
                    record.high_risk_reason or "unspecified",
                    case_id,
                )
                return {
                    "match_status": "rejected_high_risk_history",
                    "reason": (
                        f"Vendor {record.vendor_number} has a high-risk history in the registry"
                    ),
                    "matched_vendor_number": record.vendor_number,
                    "match_score": 0,
                    "blocking_issue": True,
                    "high_risk_reason": record.high_risk_reason,
                    "registry_record": {
                        "vendor_number": record.vendor_number,
                        "ssm_roc_rob_number": record.ssm_roc_rob_number,
                        "company_registered_name": record.company_registered_name,
                        "company_registered_number": record.company_registered_number,
                        "is_past_high_risk": record.is_past_high_risk,
                        "high_risk_reason": record.high_risk_reason,
                    },
                    "field_comparisons": [],
                }

        # --- Normal fuzzy matching and scoring for non-high-risk records ---
        best_match = None
        best_score = 0
        comparisons = []

        for record in db_records:
            score = 0
            max_score = 0
            field_results = []

            checks = [
                ("ssm_roc_rob_number", record.ssm_roc_rob_number),
                ("company_registered_name", record.company_registered_name),
                ("company_registered_number", record.company_registered_number),
            ]

            for field_name, registry_value in checks:
                submitted = field_map.get(field_name, "")
                max_score += 1

                if not submitted:
                    field_results.append({
                        "field_name": field_name,
                        "submitted_value": None,
                        "registry_value": registry_value,
                        "match": "not_provided",
                    })
                elif submitted == (registry_value or "").lower():
                    score += 1
                    field_results.append({
                        "field_name": field_name,
                        "submitted_value": field_map.get(field_name),
                        "registry_value": registry_value,
                        "match": "exact",
                    })
                elif InternalRegistryService._fuzzy_match(submitted, (registry_value or "").lower()):
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
                "vendor_number": record.vendor_number,
                "company_name": record.company_registered_name,
                "score": score,
                "max_score": max_score,
                "field_results": field_results,
            })

            if score > best_score:
                best_score = score
                best_match = record

        if best_score >= 2:
            match_status = "matched"
        elif best_score >= 1:
            match_status = "mismatched"
        else:
            match_status = "no_match"

        result = {
            "match_status": match_status,
            "matched_vendor_number": best_match.vendor_number if best_match else None,
            "match_score": best_score,
            "field_comparisons": comparisons,
        }

        if best_match and match_status == "matched":
            result["registry_record"] = {
                "vendor_number": best_match.vendor_number,
                "ssm_roc_rob_number": best_match.ssm_roc_rob_number,
                "company_registered_name": best_match.company_registered_name,
                "company_registered_number": best_match.company_registered_number,
                "tax_sst_gst_number": best_match.tax_sst_gst_number,
                "company_email": best_match.company_email,
                "contact_person_name": best_match.contact_person_name,
                "business_type": best_match.business_type,
                "is_past_high_risk": best_match.is_past_high_risk,
            }
        elif best_match and match_status == "mismatched":
            result["registry_record"] = {
                "vendor_number": best_match.vendor_number,
                "ssm_roc_rob_number": best_match.ssm_roc_rob_number,
                "company_registered_name": best_match.company_registered_name,
                "company_registered_number": best_match.company_registered_number,
                "is_past_high_risk": best_match.is_past_high_risk,
            }
            result["mismatch_details"] = [
                c for c in comparisons
                if c["vendor_number"] == best_match.vendor_number
                and any(f["match"] == "mismatch" for f in c["field_results"])
            ]
        else:
            result["registry_record"] = None

        return result

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
