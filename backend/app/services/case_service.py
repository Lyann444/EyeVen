from datetime import datetime
from uuid import uuid4

from app.extensions import db
from app.models import (
    VendorCase, EyeVenFormFieldValue, UploadedFile,
    VerificationRun, PolicyEvaluationRun, RoutingDecision, EmailDraft,
)
from app.services.audit_service import AuditService
from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator
from app.orchestrators.reprocessing_orchestrator import ReprocessingOrchestrator
from app.services.safety_gate import SafetyGate
from app.validators.agent_schemas import CANONICAL_FIELD_NAMES
import re


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
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).order_by(
            UploadedFile.uploaded_at.asc()
        ).all()
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
            "uploaded_files": [
                {
                    "id": file.id,
                    "original_filename": file.original_filename,
                    "file_type": file.file_type,
                    "source_category": file.source_category,
                    "upload_status": file.upload_status,
                    "uploaded_at": file.uploaded_at.isoformat() if file.uploaded_at else None,
                }
                for file in files
            ],
        }

    @staticmethod
    def mark_reviewed(case_id):
        case = VendorCase.query.get_or_404(case_id)
        case.current_state = "completed"
        db.session.commit()
        return {"id": case.id, "current_state": case.current_state}

    @staticmethod
    def run_workflow(case_id):
        return WorkflowOrchestrator.run(case_id)

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
        case = VendorCase.query.get_or_404(case_id)
        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        field_values = {
            field.field_name: SafetyGate._field_value(field)
            for field in fields
        }
        if not any(
            SafetyGate.is_follow_up_needed(field, field_values)
            for field in fields
        ):
            return {"status": "no_eligible_follow_up_fields"}

        existing_draft = EmailDraft.query.filter_by(
            vendor_case_id=case_id
        ).first()
        if existing_draft:
            return {
                "status": "already_available",
                "email_draft_id": existing_draft.id,
            }

        result = WorkflowOrchestrator._run_follow_up(case_id)
        if result.get("status") == "glm_failed":
            return result
        if case.current_state not in {
            "completed", "rejected", "flagged_high_risk",
        }:
            case.current_state = "pending_follow_up"
            db.session.commit()
        return result

    @staticmethod
    def correct_fields(case_id, updates):
        VendorCase.query.get_or_404(case_id)
        if not isinstance(updates, list) or not updates:
            raise ValueError("fields must be a non-empty array")

        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        fields_by_name = {field.field_name: field for field in fields}
        validated = []
        seen = set()
        for update in updates:
            if not isinstance(update, dict):
                raise ValueError("Each field update must be an object")
            field_name = update.get("field_name")
            value = update.get("value_text")
            if field_name not in CANONICAL_FIELD_NAMES or field_name not in fields_by_name:
                raise ValueError(f"Unsupported case field: {field_name!r}")
            if field_name in seen:
                raise ValueError(f"Duplicate field update: {field_name}")
            if not isinstance(value, str) or len(value) > 5000:
                raise ValueError(f"{field_name} must be text no longer than 5000 characters")
            value = value.strip()
            if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
                raise ValueError(f"{field_name} contains unsupported control characters")
            if field_name in {"contact_email", "company_email"} and value and not re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+", value
            ):
                raise ValueError(f"{field_name} must be a valid email address")
            if field_name == "company_website" and value and not re.match(
                r"^(https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", value
            ):
                raise ValueError("company_website must be a valid website address")
            source_file_id = update.get("source_file_id")
            confirmed = update.get("confirmed_against_source", False)
            vendor_response = update.get("vendor_response", False)
            if not isinstance(confirmed, bool):
                raise ValueError("confirmed_against_source must be a boolean")
            if not isinstance(vendor_response, bool):
                raise ValueError("vendor_response must be a boolean")
            if confirmed and source_file_id is None:
                raise ValueError(
                    "Select a supporting document before confirming its source"
                )
            if source_file_id is not None and (
                not isinstance(source_file_id, int) or isinstance(source_file_id, bool)
            ):
                raise ValueError(f"source_file_id must be an integer: {field_name}")
            source_file = None
            if source_file_id is not None:
                source_file = UploadedFile.query.filter_by(
                    id=source_file_id, vendor_case_id=case_id
                ).first()
                if not source_file:
                    raise ValueError(
                        f"Source document does not belong to this case: {field_name}"
                    )
            validated.append((
                field_name,
                value,
                source_file_id,
                confirmed,
                vendor_response,
                source_file.source_category if source_file else None,
            ))
            seen.add(field_name)

        supporting_document_fields = []
        for (
            field_name,
            value,
            source_file_id,
            confirmed,
            vendor_response,
            source_category,
        ) in validated:
            field = fields_by_name[field_name]
            original = field.value_text
            has_supporting_document = (
                source_file_id is not None
                and source_category in {"supporting_document", "corrected_upload"}
            )
            can_use_supporting_document_confidence = (
                has_supporting_document
                and not vendor_response
                and field.status not in {
                    "conflicting",
                    "mismatched_with_evidence",
                    "unverifiable",
                }
            )
            note = (
                f"PIC manual correction; not independently verified. "
                f"Previous extracted value: {original!r}."
            )
            if field.source_file_id is not None:
                note += f" Previous source file ID: {field.source_file_id}."
            if field.source_excerpt:
                note += f" Previous source excerpt: {field.source_excerpt!r}."
            if confirmed:
                note += " PIC indicated the value was checked against the source document."
            if vendor_response:
                note += " Vendor response received and transcribed by PIC."
            if can_use_supporting_document_confidence:
                note += (
                    " Demo mode: high confidence assigned because a supporting "
                    "document was selected for this value."
                )
            existing_notes = (field.notes or "").strip()
            field.notes = f"{existing_notes}\n{note}" if existing_notes else note
            field.value_text = value or None
            field.normalized_value_text = value or None
            field.value_source = (
                "vendor_response"
                if vendor_response
                else "supporting_document"
                if can_use_supporting_document_confidence
                else "unknown"
            )
            field.status = (
                "filled_from_supporting_docs"
                if value and can_use_supporting_document_confidence
                else "low_confidence"
                if value
                else "missing"
            )
            field.confidence_score = (
                0.9 if can_use_supporting_document_confidence else None
            )
            field.source_file_id = source_file_id
            field.source_excerpt = None
            if can_use_supporting_document_confidence:
                supporting_document_fields.append(field_name)

        db.session.commit()
        result = ReprocessingOrchestrator.reprocess(
            case_id, "verification", "manual_correction"
        )
        return {
            "case_id": case_id,
            "updated_fields": [item[0] for item in validated],
            "supporting_document_fields": supporting_document_fields,
            "manual_values_are_independently_verified": False,
            "reprocessing": result,
        }

    @staticmethod
    def update_follow_up_draft(case_id, draft_id, values):
        VendorCase.query.get_or_404(case_id)
        draft = EmailDraft.query.filter_by(
            id=draft_id, vendor_case_id=case_id
        ).first()
        if not draft:
            raise ValueError("Follow-up draft not found for this case")
        if draft.draft_status in {"sent", "discarded"}:
            raise ValueError("Sent or discarded drafts cannot be edited")
        if not isinstance(values, dict):
            raise ValueError("A JSON object is required")
        subject = values.get("subject_line")
        body = values.get("body_text")
        recipient = values.get("recipient_email")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
            raise ValueError("subject_line is required and must be at most 255 characters")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("body_text is required")
        if recipient is not None and (
            not isinstance(recipient, str)
            or (recipient and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient))
        ):
            raise ValueError("recipient_email must be a valid email address or null")
        draft.subject_line = subject.strip()
        draft.body_text = body
        draft.recipient_email = recipient
        draft.draft_status = "reviewed"
        db.session.commit()
        return {
            "case_id": case_id,
            "email_draft_id": draft.id,
            "draft_status": draft.draft_status,
        }

    @staticmethod
    def reprocess_stage(case_id, stage, reason):
        return ReprocessingOrchestrator.reprocess(case_id, stage, reason)

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
