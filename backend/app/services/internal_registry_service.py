import logging

from app.extensions import db
from app.models import EyeVenFormFieldValue, VendorRegistry

logger = logging.getLogger(__name__)


class InternalRegistryService:
    @staticmethod
    def _collect_all_values(field):
        """Collect all unique values for a field from value_text and document_values."""
        values = set()
        if field.value_text and field.value_text.strip():
            values.add(field.value_text.strip().lower())
        if field.document_values and isinstance(field.document_values, dict):
            for val in field.document_values.values():
                if val and str(val).strip():
                    values.add(str(val).strip().lower())
        return values

    @staticmethod
    def match_vendor(case_id):
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_map = {f.field_name: (f.value_text or "").strip().lower() for f in fields}

        # Also collect per-document values for identity fields
        identity_field_names = ("ssm_roc_rob_number", "company_registered_name", "company_registered_number")
        per_doc_values = {}
        for f in fields:
            if f.field_name in identity_field_names:
                per_doc_values[f.field_name] = InternalRegistryService._collect_all_values(f)

        submitted_ssm = field_map.get("ssm_roc_rob_number", "")
        submitted_name = field_map.get("company_registered_name", "")
        submitted_reg_no = field_map.get("company_registered_number", "")

        if not submitted_ssm and not submitted_name and not submitted_reg_no:
            return {
                "match_status": "missing_data",
                "reason": "No identifying fields available to check internal registry.",
                "matched_record": None,
                "field_comparisons": [],
            }

        # Collect ALL unique values across documents for registry lookup
        all_ssm_values = per_doc_values.get("ssm_roc_rob_number", {submitted_ssm} if submitted_ssm else set())
        all_name_values = per_doc_values.get("company_registered_name", {submitted_name} if submitted_name else set())
        all_reg_no_values = per_doc_values.get("company_registered_number", {submitted_reg_no} if submitted_reg_no else set())

        # --- Database lookup by SSM number (primary identifier) ---
        db_records = []
        for ssm_val in all_ssm_values:
            if ssm_val:
                matches = VendorRegistry.query.filter(
                    db.func.lower(VendorRegistry.ssm_roc_rob_number) == ssm_val
                ).all()
                db_records.extend(matches)

        # Fallback: search by registered name if no SSM match
        if not db_records:
            for name_val in all_name_values:
                if name_val:
                    matches = VendorRegistry.query.filter(
                        db.func.lower(VendorRegistry.company_registered_name).contains(name_val)
                    ).all()
                    db_records.extend(matches)

        # Fallback: search by company registered number
        if not db_records:
            for reg_val in all_reg_no_values:
                if reg_val:
                    matches = VendorRegistry.query.filter(
                        db.func.lower(VendorRegistry.company_registered_number) == reg_val
                    ).all()
                    db_records.extend(matches)

        # Deduplicate records
        seen_ids = set()
        unique_records = []
        for r in db_records:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                unique_records.append(r)
        db_records = unique_records

        # --- Multi-vendor detection: if documents reference different companies,
        # each may match a different registry record. This is a critical conflict.
        matched_vendor_numbers = set()
        for record in db_records:
            matched_vendor_numbers.add(record.vendor_number)
        multi_vendor_detected = len(matched_vendor_numbers) > 1 and len(db_records) > 1

        if not db_records:
            return {
                "match_status": "new_vendor", # Changed from no_match
                "reason": "This is a NEW vendor registration. No prior records exist in the internal registry. You MUST rely strictly on the external verification results to determine legitimacy.",
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

        # If documents reference different vendors in the registry, downgrade
        # from "matched" to "mismatched" — picking one vendor is wrong when
        # the documents themselves disagree.
        if multi_vendor_detected and match_status == "matched":
            match_status = "mismatched"

        result = {
            "match_status": match_status,
            "matched_vendor_number": best_match.vendor_number if best_match else None,
            "match_score": best_score,
            "multi_vendor_detected": multi_vendor_detected,
            "field_comparisons": comparisons,
        }

        if multi_vendor_detected:
            result["multi_vendor_detail"] = {
                "vendor_numbers_found": sorted(matched_vendor_numbers),
                "reason": (
                    "Documents reference multiple different vendors in the registry. "
                    "Cannot confidently match to a single vendor."
                ),
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
