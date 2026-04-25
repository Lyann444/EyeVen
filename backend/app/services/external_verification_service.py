import logging
import os

import requests
from requests.exceptions import Timeout, ConnectionError, HTTPError

from app.models import EyeVenFormFieldValue

logger = logging.getLogger(__name__)

SSM_EINFO_BASE_URL = os.getenv("SSM_EINFO_BASE_URL", "https://www.ssm-einfo.my/api")
SSM_EINFO_API_KEY = os.getenv("SSM_EINFO_API_KEY", "")

SST_INQUIRY_BASE_URL = os.getenv("SST_INQUIRY_BASE_URL", "https://sst01.customs.gov.my/api")
SST_INQUIRY_API_KEY = os.getenv("SST_INQUIRY_API_KEY", "")

LHDN_TIN_BASE_URL = os.getenv("LHDN_TIN_BASE_URL", "https://mytax.hasil.gov.my/api")
LHDN_TIN_API_KEY = os.getenv("LHDN_TIN_API_KEY", "")

API_TIMEOUT_SECONDS = int(os.getenv("EXTERNAL_API_TIMEOUT", "15"))


class ExternalVerificationService:
    @staticmethod
    def verify_identifiers(case_id):
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_map = {f.field_name: f.value_text or "" for f in fields}

        results = {
            "ssm_verification": ExternalVerificationService._verify_ssm(
                field_map.get("ssm_roc_rob_number", "")
            ),
            "tax_verification": ExternalVerificationService._verify_tax(
                field_map.get("tax_sst_gst_number", "")
            ),
            "lhdn_tin_verification": ExternalVerificationService._verify_lhdn_tin(
                field_map.get("lhdn_tin_number", "")
            ),
            "iso_verification": ExternalVerificationService._verify_iso(
                field_map.get("iso_certification_available", ""),
                field_map.get("iso_certification_number", ""),
                field_map.get("iso_certification_expiry_date", ""),
            ),
        }

        ssm_status = results["ssm_verification"]["status"]
        tax_status = results["tax_verification"]["status"]
        lhdn_status = results["lhdn_tin_verification"]["status"]

        # Any definitive failure makes the whole verification unverifiable
        if ssm_status == "failed" or tax_status == "failed" or lhdn_status == "failed":
            overall = "unverifiable"
        elif ssm_status == "verified" and tax_status == "verified" and lhdn_status in ("verified", "not_provided"):
            overall = "verified"
        elif any(s in ("verified", "partially_verified") for s in (ssm_status, tax_status, lhdn_status)):
            overall = "partially_verified"
        else:
            overall = "unverifiable"

        return {
            "overall_status": overall,
            "checks": results,
        }

    # --- SSM e-Info API ---

    @staticmethod
    def _verify_ssm(ssm_number):
        if not ssm_number or not ssm_number.strip():
            return {"status": "not_provided", "detail": "SSM/ROC/ROB number not submitted"}

        cleaned = ssm_number.strip()

        if not SSM_EINFO_API_KEY:
            logger.warning("SSM_EINFO_API_KEY not configured; falling back to format validation")
            return ExternalVerificationService._verify_ssm_format_fallback(cleaned)

        try:
            response = requests.get(
                f"{SSM_EINFO_BASE_URL}/search",
                params={"ssm_no": cleaned},
                headers={
                    "Authorization": f"Bearer {SSM_EINFO_API_KEY}",
                    "Accept": "application/json",
                },
                timeout=API_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "success" and data.get("company"):
                company = data["company"]
                return {
                    "status": "verified",
                    "detail": f"SSM number verified via SSM e-Info: {company.get('name', 'company found')}",
                    "verified_value": cleaned,
                    "api_company_name": company.get("name"),
                    "api_registration_date": company.get("registration_date"),
                    "api_company_status": company.get("status"),
                }

            if data.get("status") == "not_found":
                return {
                    "status": "failed",
                    "detail": f"SSM number {cleaned} not found in SSM e-Info registry",
                    "submitted_value": cleaned,
                }

            return {
                "status": "partially_verified",
                "detail": f"SSM e-Info returned ambiguous response for {cleaned}",
                "verified_value": cleaned,
            }

        except Timeout:
            logger.error("SSM e-Info API request timed out for %s", cleaned)
            return {
                "status": "partially_verified",
                "detail": f"SSM verification timed out; could not reach SSM e-Info for {cleaned}",
                "verified_value": cleaned,
            }
        except HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            logger.error("SSM e-Info API returned HTTP %s for %s", status_code, cleaned)
            if status_code == 404:
                return {
                    "status": "failed",
                    "detail": f"SSM number {cleaned} not found (API 404)",
                    "submitted_value": cleaned,
                }
            return {
                "status": "partially_verified",
                "detail": f"SSM e-Info returned HTTP {status_code}; unable to verify {cleaned}",
                "verified_value": cleaned,
            }
        except ConnectionError:
            logger.error("SSM e-Info API connection error for %s", cleaned)
            return {
                "status": "partially_verified",
                "detail": f"Cannot connect to SSM e-Info; network error verifying {cleaned}",
                "verified_value": cleaned,
            }
        except Exception as e:
            logger.error("Unexpected SSM e-Info error for %s: %s", cleaned, e)
            return {
                "status": "partially_verified",
                "detail": f"Unexpected error verifying SSM number {cleaned}",
                "verified_value": cleaned,
            }

    @staticmethod
    def _verify_ssm_format_fallback(ssm_number):
        import re
        cleaned = re.sub(r"[\s\-]", "", ssm_number)
        if not cleaned:
            return {"status": "not_provided", "detail": "SSM number is empty after cleaning"}
        if re.match(r"^\d{12}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "SSM format valid (12-digit) but not verified against live registry (no API key)",
                "verified_value": cleaned,
            }
        if re.match(r"^[A-Za-z0-9]{6,20}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "SSM/ROC format plausible but not verified against live registry (no API key)",
                "verified_value": cleaned,
            }
        return {
            "status": "failed",
            "detail": "SSM number format does not match expected Malaysian registration patterns",
            "submitted_value": ssm_number,
        }

    # --- SST Customs Inquiry API ---

    @staticmethod
    def _verify_tax(tax_number):
        if not tax_number or not tax_number.strip():
            return {"status": "not_provided", "detail": "Tax/SST/GST number not submitted"}

        cleaned = tax_number.strip()

        if not SST_INQUIRY_API_KEY:
            logger.warning("SST_INQUIRY_API_KEY not configured; falling back to format validation")
            return ExternalVerificationService._verify_tax_format_fallback(cleaned)

        try:
            response = requests.get(
                f"{SST_INQUIRY_BASE_URL}/inquiry",
                params={"tax_ref": cleaned},
                headers={
                    "Authorization": f"Bearer {SST_INQUIRY_API_KEY}",
                    "Accept": "application/json",
                },
                timeout=API_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "valid":
                return {
                    "status": "verified",
                    "detail": f"SST/GST number verified via Customs inquiry: {data.get('registered_name', 'registered')}",
                    "verified_value": cleaned,
                    "api_registered_name": data.get("registered_name"),
                    "api_registration_type": data.get("registration_type"),
                    "api_effective_date": data.get("effective_date"),
                }

            if data.get("status") == "invalid" or data.get("status") == "not_found":
                return {
                    "status": "failed",
                    "detail": f"Tax number {cleaned} not found or invalid per Customs SST inquiry",
                    "submitted_value": cleaned,
                }

            return {
                "status": "partially_verified",
                "detail": f"SST inquiry returned ambiguous response for {cleaned}",
                "verified_value": cleaned,
            }

        except Timeout:
            logger.error("SST inquiry API request timed out for %s", cleaned)
            return {
                "status": "partially_verified",
                "detail": f"SST verification timed out; could not reach Customs inquiry for {cleaned}",
                "verified_value": cleaned,
            }
        except HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            logger.error("SST inquiry API returned HTTP %s for %s", status_code, cleaned)
            if status_code == 404:
                return {
                    "status": "failed",
                    "detail": f"Tax number {cleaned} not found (API 404)",
                    "submitted_value": cleaned,
                }
            return {
                "status": "partially_verified",
                "detail": f"SST inquiry returned HTTP {status_code}; unable to verify {cleaned}",
                "verified_value": cleaned,
            }
        except ConnectionError:
            logger.error("SST inquiry API connection error for %s", cleaned)
            return {
                "status": "partially_verified",
                "detail": f"Cannot connect to Customs SST inquiry; network error verifying {cleaned}",
                "verified_value": cleaned,
            }
        except Exception as e:
            logger.error("Unexpected SST inquiry error for %s: %s", cleaned, e)
            return {
                "status": "partially_verified",
                "detail": f"Unexpected error verifying tax number {cleaned}",
                "verified_value": cleaned,
            }

    @staticmethod
    def _verify_tax_format_fallback(tax_number):
        import re
        cleaned = re.sub(r"[\s\-]", "", tax_number).upper()
        if not cleaned:
            return {"status": "not_provided", "detail": "Tax number is empty after cleaning"}
        if re.match(r"^SST\d{7,10}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "SST format valid but not verified against live registry (no API key)",
                "verified_value": cleaned,
            }
        if re.match(r"^GST\d{7,10}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "GST format valid but not verified against live registry (no API key)",
                "verified_value": cleaned,
            }
        if re.match(r"^[A-Za-z0-9]{6,20}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "Tax identifier format plausible but not verified against live registry (no API key)",
                "verified_value": cleaned,
            }
        return {
            "status": "failed",
            "detail": "Tax/SST/GST number format does not match expected patterns",
            "submitted_value": tax_number,
        }

    # --- LHDN TIN Verification API ---

    @staticmethod
    def _verify_lhdn_tin(tin_number):
        if not tin_number or not tin_number.strip():
            return {"status": "not_provided", "detail": "LHDN TIN number not submitted"}

        cleaned = tin_number.strip()

        if not LHDN_TIN_API_KEY:
            logger.warning("LHDN_TIN_API_KEY not configured; falling back to format validation")
            return ExternalVerificationService._verify_lhdn_tin_format_fallback(cleaned)

        try:
            response = requests.get(
                f"{LHDN_TIN_BASE_URL}/tin/verify",
                params={"tin": cleaned},
                headers={
                    "Authorization": f"Bearer {LHDN_TIN_API_KEY}",
                    "Accept": "application/json",
                },
                timeout=API_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "valid":
                return {
                    "status": "verified",
                    "detail": f"LHDN TIN verified: {data.get('taxpayer_name', 'registered taxpayer')}",
                    "verified_value": cleaned,
                    "api_taxpayer_name": data.get("taxpayer_name"),
                    "api_tax_type": data.get("tax_type"),
                    "api_registration_status": data.get("registration_status"),
                }

            if data.get("status") in ("invalid", "not_found"):
                return {
                    "status": "failed",
                    "detail": f"TIN {cleaned} not found or invalid per LHDN registry",
                    "submitted_value": cleaned,
                }

            return {
                "status": "partially_verified",
                "detail": f"LHDN TIN inquiry returned ambiguous response for {cleaned}",
                "verified_value": cleaned,
            }

        except Timeout:
            logger.error("LHDN TIN API request timed out for %s", cleaned)
            return {
                "status": "partially_verified",
                "detail": f"LHDN TIN verification timed out for {cleaned}",
                "verified_value": cleaned,
            }
        except HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            logger.error("LHDN TIN API returned HTTP %s for %s", status_code, cleaned)
            if status_code == 404:
                return {
                    "status": "failed",
                    "detail": f"TIN {cleaned} not found (API 404)",
                    "submitted_value": cleaned,
                }
            return {
                "status": "partially_verified",
                "detail": f"LHDN TIN API returned HTTP {status_code}; unable to verify {cleaned}",
                "verified_value": cleaned,
            }
        except ConnectionError:
            logger.error("LHDN TIN API connection error for %s", cleaned)
            return {
                "status": "partially_verified",
                "detail": f"Cannot connect to LHDN TIN service; network error verifying {cleaned}",
                "verified_value": cleaned,
            }
        except Exception as e:
            logger.error("Unexpected LHDN TIN error for %s: %s", cleaned, e)
            return {
                "status": "partially_verified",
                "detail": f"Unexpected error verifying LHDN TIN {cleaned}",
                "verified_value": cleaned,
            }

    @staticmethod
    def _verify_lhdn_tin_format_fallback(tin_number):
        import re
        cleaned = re.sub(r"[\s\-]", "", tin_number).upper()
        if not cleaned:
            return {"status": "not_provided", "detail": "TIN number is empty after cleaning"}
        # Malaysian TIN formats: C/XX/XXXX/XXXXX (individual) or XXXXXXXX (company)
        if re.match(r"^[A-Z]{0,2}\d{8,12}$", cleaned) or re.match(r"^[A-Z]\d{6,10}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "TIN format plausible but not verified against LHDN registry (no API key)",
                "verified_value": cleaned,
            }
        if re.match(r"^[A-Za-z0-9]{6,20}$", cleaned):
            return {
                "status": "partially_verified",
                "detail": "TIN format appears plausible but not verified against LHDN registry (no API key)",
                "verified_value": cleaned,
            }
        return {
            "status": "failed",
            "detail": "TIN number format does not match expected Malaysian LHDN patterns",
            "submitted_value": tin_number,
        }

    # --- ISO Verification (unchanged, local check) ---

    @staticmethod
    def _verify_iso(iso_available, iso_number, iso_expiry):
        if not iso_available or iso_available.lower() in ("no", "false", "n"):
            return {"status": "not_applicable", "detail": "ISO certification not claimed"}

        issues = []
        import re

        if iso_available.lower() in ("yes", "true", "y") and not iso_number:
            issues.append("ISO marked as available but no certification number provided")

        if iso_number and not re.match(r"^[A-Za-z0-9\-/]{5,30}$", iso_number.strip()):
            issues.append("ISO certification number format appears invalid")

        if iso_expiry:
            from datetime import datetime
            try:
                expiry_date = datetime.strptime(iso_expiry.strip(), "%Y-%m-%d")
                if expiry_date < datetime.utcnow():
                    issues.append(f"ISO certificate expired on {iso_expiry}")
            except ValueError:
                issues.append(f"ISO expiry date format unreadable: {iso_expiry}")
        elif iso_available.lower() in ("yes", "true", "y") and iso_number:
            issues.append("ISO expiry date not provided for claimed certification")

        if not issues:
            return {"status": "verified", "detail": "ISO certification details appear valid"}
        elif any("expired" in i or "invalid" in i for i in issues):
            return {"status": "failed", "detail": "; ".join(issues)}
        else:
            return {"status": "partially_verified", "detail": "; ".join(issues)}
