import logging
import os

from app.extensions import db
from app.models import UploadedFile, VendorCase

logger = logging.getLogger(__name__)

FORM_KEYWORDS = [
    "procurement vendor registration",
    "vendor registration form",
    "supplier registration",
    "onboarding form",
    "eyeven",
    "new registration",
    "re-registration",
    "company registered name",
    "ssm roc rob",
    "business type",
    "supplier declaration",
]

FORM_FIELD_KEYWORDS = [
    "company_registered_name",
    "company_registered_number",
    "ssm_roc_rob_number",
    "business_type",
    "bank_account_number",
    "signatory_name",
    "declaration_date",
]


class IntakeService:
    @staticmethod
    def classify_intake(case_id, uploaded_file_ids=None):
        case = VendorCase.query.get_or_404(case_id)

        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()
        if uploaded_file_ids:
            files = [f for f in files if f.id in uploaded_file_ids]

        form_file = None
        supporting_files = []
        zip_files = []

        for f in files:
            if f.source_category == "zip_bundle" and f.is_zip_parent:
                zip_files.append(f)
            else:
                is_form = IntakeService._detect_procurement_form(f)
                if is_form:
                    f.source_category = "procurement_form"
                    form_file = f
                else:
                    supporting_files.append(f)

        if form_file:
            case.intake_type = "procurement_form_present"
            case.current_state = "procurement_form_detected"
        else:
            case.intake_type = "procurement_form_absent"
            case.current_state = "procurement_form_missing"

        db.session.commit()

        return {
            "case_id": case_id,
            "intake_type": case.intake_type,
            "current_state": case.current_state,
            "form_file": {
                "id": form_file.id,
                "filename": form_file.original_filename,
            } if form_file else None,
            "supporting_file_count": len(supporting_files),
            "zip_file_count": len(zip_files),
        }

    @staticmethod
    def _detect_procurement_form(uploaded_file):
        filename_lower = uploaded_file.original_filename.lower()

        for kw in FORM_KEYWORDS:
            if kw in filename_lower:
                return True

        if uploaded_file.file_type in ("csv", "xlsx", "xls"):
            return False

        if uploaded_file.file_type in ("pdf", "png", "jpg", "jpeg"):
            return IntakeService._check_file_content_for_form(uploaded_file)

        return False

    @staticmethod
    def _check_file_content_for_form(uploaded_file):
        from app.services.ocr_service import OcrService

        if not uploaded_file.storage_path or not os.path.isfile(uploaded_file.storage_path):
            return False

        try:
            ocr_result = OcrService.run_ocr(uploaded_file.storage_path)
            full_text = ocr_result.get("full_text", "").lower()

            if not full_text:
                return False

            keyword_hits = sum(1 for kw in FORM_KEYWORDS if kw in full_text)
            field_hits = sum(1 for kw in FORM_FIELD_KEYWORDS if kw.replace("_", " ") in full_text)

            if keyword_hits >= 3 and field_hits >= 2:
                return True
            if keyword_hits >= 5:
                return True
            return False
        except Exception as e:
            logger.warning("Form detection OCR failed for file %s: %s", uploaded_file.id, e)
            return False