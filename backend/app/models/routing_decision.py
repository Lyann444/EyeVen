from datetime import datetime
from app.extensions import db


class RoutingDecision(db.Model):
    __tablename__ = "routing_decisions"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    verification_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("verification_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_evaluation_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("policy_evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_code = db.Column(
        db.Enum(
            "complete_verified",
            "follow_up_required",
            "flagged_high_risk",
            "rejected",
            "manual_review_required",
        ),
        nullable=False,
    )
    recommended_procurement_action = db.Column(db.String(255), nullable=True)
    decision_reasoning_summary = db.Column(db.Text, nullable=False)
    confidence_score = db.Column(db.Numeric(5, 4), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
