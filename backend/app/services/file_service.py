import hashlib
import logging
import os
import uuid
from datetime import datetime

from flask import current_app
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import UploadedFile, VendorCase

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "xlsx", "xls", "csv", "png", "jpg", "jpeg", "zip"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "image/png",
    "image/jpeg",
    "application/zip",
    "application/x-zip-compressed",
}


class FileService:
    @staticmethod
    def upload_files(case_id, files, email_content=None):
        case = VendorCase.query.get_or_404(case_id)

        if not files:
            return {"error": "No files provided"}, 400

        upload_dir = FileService._get_upload_dir(case_id)
        os.makedirs(upload_dir, exist_ok=True)

        uploaded = []
        for f in files:
            result = FileService._process_single_file(f, case_id, upload_dir, "supporting_document")
            if result:
                uploaded.append(result)

        if any(r["source_category"] == "procurement_form" for r in uploaded):
            case.intake_type = "procurement_form_present"
        else:
            case.intake_type = "procurement_form_absent"

        case.current_state = "intake_received"
        db.session.commit()

        return {
            "case_id": case_id,
            "uploaded_files": uploaded,
            "intake_type": case.intake_type,
            "current_state": case.current_state,
        }

    @staticmethod
    def upload_corrected_files(case_id, files):
        case = VendorCase.query.get_or_404(case_id)

        if not files:
            return {"error": "No files provided"}, 400
        FileService._validate_files(files)

        upload_dir = FileService._get_upload_dir(case_id)
        os.makedirs(upload_dir, exist_ok=True)

        uploaded = []
        for f in files:
            result = FileService._process_single_file(f, case_id, upload_dir, "corrected_upload")
            if result:
                uploaded.append(result)

        case.current_state = "reprocessing"
        db.session.commit()

        return {
            "case_id": case_id,
            "uploaded_files": uploaded,
            "current_state": case.current_state,
        }

    @staticmethod
    def upload_recovery_files(case_id, files):
        case = VendorCase.query.get_or_404(case_id)
        if not files:
            raise ValueError("No files provided")
        FileService._validate_files(files)

        upload_dir = FileService._get_upload_dir(case_id)
        os.makedirs(upload_dir, exist_ok=True)
        uploaded = [
            FileService._process_single_file(
                file, case_id, upload_dir, "corrected_upload"
            )
            for file in files
        ]
        accepted = [item for item in uploaded if item["upload_status"] != "duplicate"]
        if not accepted:
            return {
                "case_id": case_id,
                "status": "duplicate_upload",
                "uploaded_files": uploaded,
            }

        case.current_state = "intake_received"
        db.session.commit()
        return {
            "case_id": case_id,
            "status": "uploaded",
            "uploaded_files": uploaded,
            "current_state": case.current_state,
            "message": "Document uploaded. Run the workflow to process it.",
        }

    @staticmethod
    def _validate_files(files):
        max_bytes = current_app.config["MAX_UPLOAD_SIZE_MB"] * 1024 * 1024
        for file_storage in files:
            filename = secure_filename(file_storage.filename or "")
            if not filename:
                raise ValueError("Each upload must have a filename")
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in ALLOWED_EXTENSIONS:
                raise ValueError(f"Unsupported file type: {filename}")
            file_storage.seek(0, 2)
            file_size = file_storage.tell()
            file_storage.seek(0)
            if file_size == 0:
                raise ValueError(f"File is empty: {filename}")
            if file_size > max_bytes:
                raise ValueError(
                    f"File exceeds the {current_app.config['MAX_UPLOAD_SIZE_MB']} MB limit: {filename}"
                )

    @staticmethod
    def _process_single_file(file_storage, case_id, upload_dir, source_category):
        filename = secure_filename(file_storage.filename or "unnamed")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in ALLOWED_EXTENSIONS:
            logger.warning("Rejected file with unsupported extension: %s", filename)
            return None

        file_storage.seek(0, 2)
        file_size = file_storage.tell()
        file_storage.seek(0)

        max_bytes = current_app.config["MAX_UPLOAD_SIZE_MB"] * 1024 * 1024
        if file_size > max_bytes:
            logger.warning("Rejected oversized file: %s (%d bytes)", filename, file_size)
            return None

        mime_type = file_storage.mimetype or "application/octet-stream"

        stored_name = f"{uuid.uuid4().hex}_{filename}"
        storage_path = os.path.join(upload_dir, stored_name)
        file_storage.save(storage_path)

        checksum = FileService._compute_sha256(storage_path)
        existing = UploadedFile.query.filter_by(
            vendor_case_id=case_id,
            checksum_sha256=checksum,
        ).first()
        if existing:
            os.remove(storage_path)
            return {
                "id": existing.id,
                "original_filename": existing.original_filename,
                "file_type": existing.file_type,
                "source_category": existing.source_category,
                "upload_status": "duplicate",
            }

        is_zip = ext == "zip"
        if is_zip:
            source_category = "zip_bundle"

        record = UploadedFile(
            vendor_case_id=case_id,
            original_filename=filename,
            stored_filename=stored_name,
            file_type=ext,
            mime_type=mime_type,
            file_size_bytes=file_size,
            source_category=source_category,
            storage_path=storage_path,
            checksum_sha256=checksum,
            is_zip_parent=is_zip,
            upload_status="stored",
        )
        db.session.add(record)
        db.session.flush()

        return {
            "id": record.id,
            "original_filename": filename,
            "file_type": ext,
            "source_category": record.source_category,
            "upload_status": "stored",
        }

    @staticmethod
    def _get_upload_dir(case_id):
        base = current_app.config.get("UPLOAD_FOLDER", "uploads")
        return os.path.join(base, str(case_id))

    @staticmethod
    def _compute_sha256(file_path):
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
