from datetime import datetime
from app.extensions import db


class VendorRegistry(db.Model):
    __tablename__ = "vendor_registry"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    vendor_number = db.Column(db.String(50), nullable=False, unique=True)
    ssm_roc_rob_number = db.Column(db.String(50), nullable=False, index=True)
    company_registered_name = db.Column(db.String(255), nullable=False)
    company_registered_number = db.Column(db.String(50), nullable=True)
    tax_sst_gst_number = db.Column(db.String(50), nullable=True)
    company_email = db.Column(db.String(255), nullable=True)
    contact_person_name = db.Column(db.String(255), nullable=True)
    business_type = db.Column(db.String(50), nullable=True)
    is_past_high_risk = db.Column(db.Boolean, nullable=False, default=False)
    high_risk_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
