from datetime import datetime
from uuid import uuid4

from app.extensions import db
from app.models import (
    VendorCase, EyeVenFormFieldValue, UploadedFile,
    VerificationRun, PolicyEvaluationRun, RoutingDecision, EmailDraft,
)
from app.orchestrators.extraction_orchestrator import ExtractionOrchestrator
from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator
from app.orchestrators.reprocessing_orchestrator import ReprocessingOrchestrator


class CaseService:
    @staticmethod
    def create_case(data):
        # Use a temporary unique value to obtain the database ID, then derive
        # a human-readable reference from that ID. Existing case references
        # are never changed.
        case = VendorCase(
            case_reference=f"TEMP-{uuid4().hex}",
            vendor_display_name=data.get("vendor_display_name"),
            intake_type=data["intake_type"],
            registration_type=data.get("registration_type"),
            current_vendor_number=data.get("current_vendor_number"),
            current_state="uploaded",
        )
        db.session.add(case)
        db.session.flush()

        generated_reference = f"EV-{datetime.utcnow().year}-{case.id:06d}"
        # Protect legacy databases where a user may already have used this format.
        if VendorCase.query.filter_by(case_reference=generated_reference).first():
            generated_reference = f"{generated_reference}-{uuid4().hex[:6].upper()}"
        case.case_reference = generated_reference

        CaseService._seed_form_fields(case.id)

        db.session.commit()
        return {"id": case.id, "case_reference": case.case_reference, "current_state": case.current_state}

    @staticmethod
    def list_cases():
        cases = VendorCase.query.order_by(VendorCase.updated_at.desc()).all()
        return [
            {
                "id": c.id,
                "case_reference": c.case_reference,
                "vendor_display_name": c.vendor_display_name,
                "current_state": c.current_state,
                "risk_band": c.risk_band,
                "intake_type": c.intake_type,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in cases
        ]

    @staticmethod
    def get_case_detail(case_id):
        case = VendorCase.query.get_or_404(case_id)
        return {
            "id": case.id,
            "case_reference": case.case_reference,
            "vendor_display_name": case.vendor_display_name,
            "intake_type": case.intake_type,
            "registration_type": case.registration_type,
            "current_vendor_number": case.current_vendor_number,
            "current_state": case.current_state,
            "risk_band": case.risk_band,
            "latest_verification_status": case.latest_verification_status,
            "latest_policy_status": case.latest_policy_status,
            "latest_routing_decision": case.latest_routing_decision,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        }

    @staticmethod
    def mark_reviewed(case_id):
        case = VendorCase.query.get_or_404(case_id)
        case.current_state = "completed"
        db.session.commit()
        return {"id": case.id, "current_state": case.current_state}

    @staticmethod
    def run_workflow(case_id):
        case = VendorCase.query.get_or_404(case_id)

        if case.current_state in ("uploaded", "intake_received"):
            ExtractionOrchestrator.run(case_id)
            case = VendorCase.query.get(case_id)

        workflow_result = WorkflowOrchestrator.run(case_id)
        return workflow_result

    @staticmethod
    def rerun_affected_stages(case_id):
        return ReprocessingOrchestrator.run(case_id)

    @staticmethod
    def get_form_alignment(case_id):
        case = VendorCase.query.get_or_404(case_id)
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        if not fields:
            CaseService._seed_form_fields(case_id)
            db.session.commit()
            fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        return {
            "case_id": case_id,
            "intake_type": case.intake_type,
            "fields": [
                {
                    "section_name": f.section_name,
                    "field_name": f.field_name,
                    "field_label": f.field_label,
                    "value_text": f.value_text,
                    "normalized_value_text": f.normalized_value_text,
                    "value_source": f.value_source,
                    "status": f.status,
                    "confidence_score": float(f.confidence_score) if f.confidence_score else None,
                    "source_excerpt": f.source_excerpt,
                    "notes": f.notes,
                }
                for f in fields
            ],
            "completeness": {
                "total": len(fields),
                "populated": sum(1 for f in fields if f.value_text),
                "missing": sum(1 for f in fields if f.status == "missing"),
                "conflicting": sum(1 for f in fields if f.status == "conflicting"),
            },
        }

    @staticmethod
    def get_verification(case_id):
        latest = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()
        if not latest:
            return {"case_id": case_id, "status": "no_verification_run_yet"}
        return {
            "case_id": case_id,
            "run_id": latest.id,
            "legitimacy_status": latest.legitimacy_status,
            "mismatch_severity": latest.mismatch_severity,
            "summary": latest.summary,
            "key_findings": latest.key_findings,
            "confidence_score": float(latest.confidence_score) if latest.confidence_score else None,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
        }

    @staticmethod
    def get_policy(case_id):
        latest = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()
        if not latest:
            return {"case_id": case_id, "status": "no_policy_run_yet"}
        return {
            "case_id": case_id,
            "run_id": latest.id,
            "risk_band": latest.risk_band,
            "blocking_issue_count": latest.blocking_issue_count,
            "recoverable_issue_count": latest.recoverable_issue_count,
            "policy_summary": latest.policy_summary,
            "risk_flags": latest.risk_flags,
            "policy_confidence_score": float(latest.policy_confidence_score) if latest.policy_confidence_score else None,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
        }

    @staticmethod
    def get_routing(case_id):
        latest = RoutingDecision.query.filter_by(
            vendor_case_id=case_id
        ).order_by(RoutingDecision.created_at.desc()).first()
        if not latest:
            return {"case_id": case_id, "status": "no_routing_decision_yet"}
        return {
            "case_id": case_id,
            "decision_id": latest.id,
            "decision_code": latest.decision_code,
            "recommended_procurement_action": latest.recommended_procurement_action,
            "decision_reasoning_summary": latest.decision_reasoning_summary,
            "decision_evidence": AuditService._parse_json_list(latest.decision_evidence),
            "confidence_score": float(latest.confidence_score) if latest.confidence_score else None,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
        }

    @staticmethod
    def create_follow_up(case_id):
        result = WorkflowOrchestrator._run_follow_up(case_id)
        return result

    @staticmethod
    def _seed_form_fields(case_id):
        CANONICAL_FIELDS = [
            ("registration_type", "new_registration", "Registration Type (New Registration)"),
            ("registration_type", "re_registration", "Registration Type (Re-registration)"),
            ("registration_type", "current_vendor_number", "Current Vendor Number (if re-registration)"),
            ("company_general", "company_registered_name", "Company Registered Name"),
            ("company_general", "company_registered_number", "Company Registered Number"),
            ("company_general", "company_registered_address", "Company Registered Address"),
            ("company_general", "company_website", "Company Website"),
            ("company_general", "company_email", "Company Email"),
            ("company_general", "main_office_phone_number", "Main Office Phone Number"),
            ("company_general", "year_of_establishment", "Year of Establishment"),
            ("company_general", "business_type", "Business Type"),
            ("company_general", "business_type_other", "Business Type (Other)"),
            ("primary_contact", "contact_person_name", "Contact Person Name"),
            ("primary_contact", "designation", "Designation"),
            ("primary_contact", "contact_email", "Contact Email"),
            ("primary_contact", "contact_mobile_number", "Contact Mobile Number"),
            ("financial", "bank_name", "Bank Name"),
            ("financial", "bank_account_name", "Bank Account Name"),
            ("financial", "bank_account_number", "Bank Account Number"),
            ("financial", "latest_annual_revenue_range", "Latest Annual Revenue Range"),
            ("registration", "ssm_roc_rob_number", "SSM / ROC / ROB Number"),
            ("registration", "tax_sst_gst_number", "Tax / SST / GST Number"),
            ("registration", "iso_certification_available", "ISO Certification Available"),
            ("registration", "iso_certification_number", "ISO Certification Number"),
            ("registration", "iso_certification_expiry_date", "ISO Certification Expiry Date"),
            ("risk_declaration", "main_business_activities", "Main Business Activities"),
            ("risk_declaration", "top_3_major_clients", "Top 3 Major Clients"),
            ("risk_declaration", "legal_disputes_past_5_years", "Legal Disputes Past 5 Years"),
            ("risk_declaration", "legal_disputes_details", "Legal Disputes Details"),
            ("risk_declaration", "outsource_any_part_of_services", "Outsource Any Part of Services"),
            ("risk_declaration", "outsourcing_details", "Outsourcing Details"),
            ("risk_declaration", "number_of_employees", "Number of Employees"),
            ("supplier_declaration", "authorized_signature_present", "Authorized Signature Present"),
            ("supplier_declaration", "signatory_name", "Signatory Name"),
            ("supplier_declaration", "signatory_designation", "Signatory Designation"),
            ("supplier_declaration", "signatory_ic_or_passport_number", "Signatory IC / Passport Number"),
            ("supplier_declaration", "declaration_date", "Declaration Date"),
            ("supplier_declaration", "company_chop_present", "Company Chop Present"),
        ]

        for section, field_name, field_label in CANONICAL_FIELDS:
            db.session.add(
                EyeVenFormFieldValue(
                    vendor_case_id=case_id,
                    section_name=section,
                    field_name=field_name,
                    field_label=field_label,
                    value_source="unknown",
                    status="missing",
                )
            )
