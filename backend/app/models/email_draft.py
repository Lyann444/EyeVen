from datetime import datetime
from app.extensions import db


class EmailDraft(db.Model):
    __tablename__ = "email_drafts"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_cases.id", ondelete="CASCADE"), nullable=False
    )
    routing_decision_id = db.Column(
        db.BigInteger,
        db.ForeignKey("routing_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    draft_type = db.Column(db.Enum("vendor_follow_up"), nullable=False)
    recipient_email = db.Column(db.String(255), nullable=True)
    subject_line = db.Column(db.String(255), nullable=False)
    body_text = db.Column(db.Text, nullable=False)
    draft_status = db.Column(
        db.Enum("generated", "reviewed", "sent", "discarded"),
        nullable=False,
        default="generated",
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)

    routing_decision = db.relationship("RoutingDecision")
