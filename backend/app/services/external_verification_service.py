import logging
import re
from datetime import datetime

from app.models import EyeVenFormFieldValue
from app.services.safety_gate import SafetyGate

logger = logging.getLogger(__name__)


class ExternalVerificationService:
    @staticmethod
    def verify_identifiers(case_id, internal_registry_match=None):
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_map = {f.field_name: f.value_text or "" for f in fields}

        registry_matched = (
            isinstance(internal_registry_match, dict)
            and internal_registry_match.get("match_status") == "matched"
        )
        registry_corroborated_fields = (
            SafetyGate._registry_corroborated_fields(internal_registry_match)
        )
        ssm_field = "ssm_roc_rob_number"
        tax_field = "tax_sst_gst_number"
        results = {
            "ssm_verification": (
                {
                    "status": "internally_matched",
                    "detail": (
                        "SSM/ROC/ROB identifier exactly matches the selected "
                        "internal registry record; no external registry was contacted"
                    ),
                    "verified_value": field_map.get(ssm_field, ""),
                }
                if ssm_field in registry_corroborated_fields
                else ExternalVerificationService._verify_ssm(
                    field_map.get(ssm_field, "")
                )
            ),
            "tax_verification": (
                {
                    "status": "internally_matched",
                    "detail": (
                        "Tax/SST/GST identifier exactly matches the selected "
                        "internal registry record; no external tax registry was contacted"
                    ),
                    "verified_value": field_map.get(tax_field, ""),
                }
                if tax_field in registry_corroborated_fields
                else ExternalVerificationService._verify_tax(
                    field_map.get(tax_field, "")
                )
            ),
            "iso_verification": ExternalVerificationService._verify_iso(
                field_map.get("iso_certification_available", ""),
                field_map.get("iso_certification_number", ""),
                field_map.get("iso_certification_expiry_date", ""),
            ),
        }

        ssm_status = results["ssm_verification"]["status"]
        tax_status = results["tax_verification"]["status"]

        if (
            ssm_status == "internally_matched"
            or tax_status == "internally_matched"
        ):
            overall = "internally_corroborated"
        elif ssm_status == "verified" and tax_status == "verified":
            overall = "verified"
        elif ssm_status in ("verified", "partially_verified") and tax_status in ("verified", "partially_verified"):
            overall = "partially_verified"
        elif ssm_status == "failed" or tax_status == "failed":
            overall = "unverifiable"
        else:
            overall = "partially_verified"

        return {
            "overall_status": overall,
            "verification_source": (
                "internal_registry_and_local_format_checks"
                if registry_corroborated_fields
                else "local_format_checks_only"
            ),
            "internal_registry_match_status": (
                "matched" if registry_matched else
                internal_registry_match.get("match_status")
                if isinstance(internal_registry_match, dict) else None
            ),
            "internally_corroborated_fields": sorted(
                registry_corroborated_fields
            ),
            "external_registry_contacted": False,
            "checks": results,
        }

    @staticmethod
    def _verify_ssm(ssm_number):
        if not ssm_number:
            return {"status": "not_provided", "detail": "SSM/ROC/ROB number not submitted"}

        legacy_registration = re.fullmatch(
            r"(\d{12})\s*\(([A-Za-z0-9-]{1,20})\)",
            ssm_number.strip(),
        )
        if legacy_registration:
            return {
                "status": "partially_verified",
                "detail": (
                    "SSM number format appears valid with a legacy registration "
                    "number; registry verification was not performed"
                ),
                "verified_value": legacy_registration.group(1),
            }

        cleaned = re.sub(r"[\s\-]", "", ssm_number)

        if not cleaned:
            return {"status": "not_provided", "detail": "SSM number is empty after cleaning"}

        # Malaysian SSM format: YYYYNNNNNNNNN (e.g., 202001012345)
        if re.match(r"^\d{12}$", cleaned):
            return {
                "status": "verified",
                "detail": "SSM number format valid (12-digit Malaysian registration format)",
                "verified_value": cleaned,
            }

        # ROC format: alphanumeric
        if re.match(r"^[A-Za-z0-9]{6,20}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "SSM/ROC number format appears plausible but could not fully verify against registry",
                "verified_value": cleaned,
            }

        return {
            "status": "failed",
            "detail": "SSM number format does not match expected Malaysian registration patterns",
            "submitted_value": ssm_number,
        }

    @staticmethod
    def _verify_tax(tax_number):
        if not tax_number:
            return {"status": "not_provided", "detail": "Tax/SST/GST number not submitted"}

        cleaned = re.sub(r"[\s\-]", "", tax_number).upper()

        if not cleaned:
            return {"status": "not_provided", "detail": "Tax number is empty after cleaning"}

        # SST format: SST + digits
        if re.match(r"^SST\d{7,10}$", cleaned):
            return {
                "status": "verified",
                "detail": "SST number format valid",
                "verified_value": cleaned,
            }

        # GST format: GST + digits
        if re.match(r"^GST\d{7,10}$", cleaned):
            return {
                "status": "verified",
                "detail": "GST number format valid",
                "verified_value": cleaned,
            }

        # Generic tax ID: alphanumeric
        if re.match(r"^[A-Za-z0-9]{6,20}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "Tax identifier format appears plausible but could not fully verify",
                "verified_value": cleaned,
            }

        return {
            "status": "failed",
            "detail": "Tax/SST/GST number format does not match expected patterns",
            "submitted_value": tax_number,
        }

    @staticmethod
    def _verify_iso(iso_available, iso_number, iso_expiry):
        if not iso_available or iso_available.lower() in ("no", "false", "n"):
            return {"status": "not_applicable", "detail": "ISO certification not claimed"}

        issues = []
        if iso_available.lower() in ("yes", "true", "y") and not iso_number:
            issues.append("ISO marked as available but no certification number provided")

        if iso_number and not re.match(r"^[A-Za-z0-9\-/]{5,30}$", iso_number.strip()):
            issues.append("ISO certification number format appears invalid")

        if iso_expiry:
            expiry_date = ExternalVerificationService._parse_iso_expiry(iso_expiry)
            if expiry_date is not None:
                if expiry_date < datetime.utcnow():
                    issues.append(f"ISO certificate expired on {iso_expiry}")
            else:
                issues.append(f"ISO expiry date format unreadable: {iso_expiry}")
        elif iso_available.lower() in ("yes", "true", "y") and iso_number:
            issues.append("ISO expiry date not provided for claimed certification")

        if not issues:
            return {"status": "verified", "detail": "ISO certification details appear valid"}
        elif any("expired" in i or "invalid" in i for i in issues):
            return {"status": "failed", "detail": "; ".join(issues)}
        else:
            return {"status": "partially_verified", "detail": "; ".join(issues)}

    @staticmethod
    def _parse_iso_expiry(value):
        for date_format in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(value.strip(), date_format)
            except ValueError:
                continue
        return None
