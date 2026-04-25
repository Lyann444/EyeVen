from datetime import datetime
from app.extensions import db


class EyeVenFormFieldValue(db.Model):
    __tablename__ = "eyeven_form_field_values"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    section_name = db.Column(db.String(100), nullable=False)
    field_name = db.Column(db.String(100), nullable=False)
    field_label = db.Column(db.String(150), nullable=False)
    value_text = db.Column(db.Text, nullable=True)
    normalized_value_text = db.Column(db.Text, nullable=True)
    value_source = db.Column(
        db.Enum(
            "procurement_form",
            "supporting_document",
            "derived_from_multiple_sources",
            "unknown",
        ),
        nullable=False,
    )
    status = db.Column(
        db.Enum(
            "provided_in_form",
            "matched_with_evidence",
            "mismatched_with_evidence",
            "filled_from_supporting_docs",
            "missing",
            "conflicting",
            "unverifiable",
            "low_confidence",
            "declaration_missing",
        ),
        nullable=False,
    )
    confidence_score = db.Column(db.Numeric(5, 4), nullable=True)
    source_file_id = db.Column(
        db.BigInteger,
        db.ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_excerpt = db.Column(db.Text, nullable=True)
    document_values = db.Column(db.JSON, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    source_file = db.relationship("UploadedFile")

    __table_args__ = (
        db.UniqueConstraint("vendor_case_id", "field_name", name="uk_case_field"),
    )
