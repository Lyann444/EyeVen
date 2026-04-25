from datetime import datetime
from app.extensions import db


class UploadedFile(db.Model):
    __tablename__ = "uploaded_files"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    reprocessing_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("reprocessing_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size_bytes = db.Column(db.BigInteger, nullable=False)
    source_category = db.Column(
        db.Enum(
            "procurement_form", "supporting_document", "zip_bundle", "corrected_upload",
            "follow_up_upload",
        ),
        nullable=False,
    )
    storage_path = db.Column(db.String(500), nullable=False)
    checksum_sha256 = db.Column(db.String(64), nullable=False)
    is_zip_parent = db.Column(db.Boolean, nullable=False, default=False)
    parent_uploaded_file_id = db.Column(
        db.BigInteger,
        db.ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    upload_status = db.Column(db.String(50), nullable=False)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    children = db.relationship(
        "UploadedFile",
        backref=db.backref("parent", remote_side=[id]),
        lazy="dynamic",
    )
