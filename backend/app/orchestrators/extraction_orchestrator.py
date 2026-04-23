import logging
import os

from flask import current_app

from app.extensions import db
from app.models import UploadedFile, VendorCase
from app.services.zip_service import ZipService
from app.services.parser_service import ParserService
from app.services.ocr_service import OcrService
from app.services.intake_service import IntakeService

logger = logging.getLogger(__name__)

STRUCTURED_EXTENSIONS = {"csv", "xlsx", "xls"}
OCR_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp"}


class ExtractionOrchestrator:
    @staticmethod
    def run(case_id, uploaded_file_ids=None):
        case = VendorCase.query.get_or_404(case_id)
        case.current_state = "extracting"
        db.session.commit()

        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()
        if uploaded_file_ids:
            files = [f for f in files if f.id in uploaded_file_ids]

        zip_files = [f for f in files if f.is_zip_parent]
        child_files = []
        for zf in zip_files:
            child_files.extend(ExtractionOrchestrator._extract_zip_contents(case_id, zf))

        all_files = [f for f in files if not f.is_zip_parent] + child_files

        intake_result = IntakeService.classify_intake(case_id)

        extraction_results = []
        for f in all_files:
            result = ExtractionOrchestrator._extract_single_file(f)
            extraction_results.append(result)

        case.current_state = "intake_classified"
        db.session.commit()

        return {
            "case_id": case_id,
            "current_state": case.current_state,
            "intake_type": intake_result["intake_type"],
            "files_processed": len(all_files),
            "zip_files_extracted": len(child_files),
            "extraction_results": extraction_results,
        }

    @staticmethod
    def _extract_zip_contents(case_id, zip_file):
        if not zip_file.storage_path or not os.path.isfile(zip_file.storage_path):
            return []

        target_dir = os.path.join(
            os.path.dirname(zip_file.storage_path), f"zip_{zip_file.id}"
        )
        max_files = current_app.config.get("MAX_ZIP_FILES", 20)

        result = ZipService.extract_zip(zip_file.storage_path, target_dir, max_files)

        child_records = []
        for extracted in result.get("extracted_files", []):
            record = UploadedFile(
                vendor_case_id=case_id,
                original_filename=extracted["filename"],
                stored_filename=extracted["filename"],
                file_type=os.path.splitext(extracted["filename"])[1].lstrip(".").lower(),
                mime_type="application/octet-stream",
                file_size_bytes=extracted.get("file_size", 0),
                source_category="supporting_document",
                storage_path=extracted["extracted_path"],
                checksum_sha256="",
                is_zip_parent=False,
                parent_uploaded_file_id=zip_file.id,
                upload_status="extracted_from_zip",
            )
            db.session.add(record)
            child_records.append(record)

        db.session.flush()
        return child_records

    @staticmethod
    def _extract_single_file(uploaded_file):
        file_path = uploaded_file.storage_path
        ext = uploaded_file.file_type.lower()

        if not file_path or not os.path.isfile(file_path):
            return {
                "file_id": uploaded_file.id,
                "filename": uploaded_file.original_filename,
                "status": "file_not_found",
                "extracted_text": "",
            }

        if ext in STRUCTURED_EXTENSIONS:
            parsed = ParserService.parse_file(file_path)
            return {
                "file_id": uploaded_file.id,
                "filename": uploaded_file.original_filename,
                "extraction_method": "deterministic_parser",
                "status": parsed.get("status", "completed"),
                "parsed_data": parsed,
            }

        if ext in OCR_EXTENSIONS:
            ocr_result = OcrService.run_ocr(file_path)
            return {
                "file_id": uploaded_file.id,
                "filename": uploaded_file.original_filename,
                "extraction_method": "paddleocr",
                "status": ocr_result.get("status", "completed"),
                "full_text": ocr_result.get("full_text", ""),
                "page_count": ocr_result.get("page_count", 0),
                "text_block_count": len(ocr_result.get("text_blocks", [])),
            }

        return {
            "file_id": uploaded_file.id,
            "filename": uploaded_file.original_filename,
            "extraction_method": "none",
            "status": "unsupported_format",
            "extracted_text": "",
        }
