from datetime import datetime
from app.extensions import db


class VerificationRun(db.Model):
    __tablename__ = "verification_runs"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    reprocessing_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("reprocessing_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_status = db.Column(db.String(50), nullable=False)
    legitimacy_status = db.Column(
        db.Enum(
            "verified",
            "verified_with_minor_gaps",
            "conflicting",
            "partially_verifiable",
            "unverifiable",
            "suspicious",
        ),
        nullable=True,
    )
    mismatch_severity = db.Column(
        db.Enum("minor", "moderate", "critical"), nullable=True
    )
    summary = db.Column(db.Text, nullable=True)
    key_findings = db.Column(db.LongText, nullable=True)
    confidence_score = db.Column(db.Numeric(5, 4), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
