from datetime import datetime
from app.extensions import db


class PolicyEvaluationRun(db.Model):
    __tablename__ = "policy_evaluation_runs"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    verification_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("verification_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    reprocessing_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("reprocessing_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    risk_band = db.Column(
        db.Enum("low", "medium", "high", "critical"), nullable=True
    )
    blocking_issue_count = db.Column(db.Integer, nullable=False, default=0)
    recoverable_issue_count = db.Column(db.Integer, nullable=False, default=0)
    policy_summary = db.Column(db.Text, nullable=True)
    risk_flags = db.Column(db.Text, nullable=True)
    policy_confidence_score = db.Column(db.Numeric(5, 4), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
