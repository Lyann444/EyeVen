from datetime import datetime

from app.extensions import db


class WorkflowExecutionTrace(db.Model):
    """Durable, non-sensitive record of an individual workflow stage."""

    __tablename__ = "workflow_execution_traces"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_case_id = db.Column(
        db.BigInteger,
        db.ForeignKey("vendor_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = db.Column(db.String(80), nullable=False)
    state_before = db.Column(db.String(100), nullable=False)
    state_after = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(80), nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=False)
    duration_ms = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vendor_case = db.relationship(
        "VendorCase", backref=db.backref("workflow_execution_traces", lazy="dynamic")
    )
