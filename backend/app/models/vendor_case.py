from datetime import datetime
from app.extensions import db


class VendorCase(db.Model):
    __tablename__ = "vendor_cases"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    case_reference = db.Column(db.String(50), nullable=False, unique=True)
    vendor_display_name = db.Column(db.String(255), nullable=True)
    intake_type = db.Column(
        db.Enum("procurement_form_present", "procurement_form_absent"),
        nullable=False,
    )
    registration_type = db.Column(
        db.Enum("new_registration", "re_registration"), nullable=True
    )
    current_vendor_number = db.Column(db.String(100), nullable=True)
    current_state = db.Column(db.String(100), nullable=False)
    risk_band = db.Column(
        db.Enum("low", "medium", "high", "critical"), nullable=True
    )
    latest_verification_status = db.Column(db.String(100), nullable=True)
    latest_policy_status = db.Column(db.String(100), nullable=True)
    latest_routing_decision = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    closed_at = db.Column(db.DateTime, nullable=True)

    uploaded_files = db.relationship(
        "UploadedFile", backref="vendor_case", lazy="dynamic"
    )
    form_field_values = db.relationship(
        "EyeVenFormFieldValue", backref="vendor_case", lazy="dynamic"
    )
    verification_runs = db.relationship(
        "VerificationRun", backref="vendor_case", lazy="dynamic"
    )
    policy_evaluation_runs = db.relationship(
        "PolicyEvaluationRun", backref="vendor_case", lazy="dynamic"
    )
    routing_decisions = db.relationship(
        "RoutingDecision", backref="vendor_case", lazy="dynamic"
    )
    email_drafts = db.relationship(
        "EmailDraft", backref="vendor_case", lazy="dynamic"
    )
    reprocessing_runs = db.relationship(
        "ReprocessingRun", backref="vendor_case", lazy="dynamic"
    )
