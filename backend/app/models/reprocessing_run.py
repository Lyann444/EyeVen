from datetime import datetime
from app.extensions import db


class ReprocessingRun(db.Model):
    __tablename__ = "reprocessing_runs"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type = db.Column(
        db.Enum("corrected_upload", "manual_rerun", "follow_up_upload"), nullable=False
    )
    reason_summary = db.Column(db.Text, nullable=True)
    previous_case_state = db.Column(db.String(100), nullable=False)
    new_case_state = db.Column(db.String(100), nullable=True)
    run_status = db.Column(
        db.Enum("started", "completed", "failed"), nullable=False
    )
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
