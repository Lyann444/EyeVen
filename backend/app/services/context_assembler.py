import json
from app.extensions import db
from app.models import (
    VendorCase,
    UploadedFile,
    EyeVenFormFieldValue,
    VerificationRun,
    PolicyEvaluationRun,
    RoutingDecision,
)
from app.services.internal_registry_service import InternalRegistryService
from app.services.external_verification_service import ExternalVerificationService
from app.services.safety_gate import FOLLOW_UP_FIELDS, SafetyGate


class ContextAssembler:
    ROUTING_REVIEW_STATUSES = {
        "missing",
        "conflicting",
        "unverifiable",
        "low_confidence",
        "declaration_missing",
        "mismatched_with_evidence",
    }

    @staticmethod
    def _routing_field_evidence(fields) -> dict:
        """Build a compact, structured summary of unresolved field evidence.

        Values and source excerpts are intentionally omitted here to reduce
        unnecessary exposure of vendor PII in the routing prompt.
        """
        status_counts = {}
        unresolved_fields = []
        pic_supplied_fields = []
        vendor_responses = []

        for field in fields:
            if field.field_name not in FOLLOW_UP_FIELDS:
                continue
            if SafetyGate.has_pic_supplied_value(field):
                pic_supplied_fields.append(field.field_name)
            if SafetyGate.has_vendor_response(field):
                vendor_responses.append(field.field_name)
                continue
            if SafetyGate.has_pic_supplied_value(field):
                continue
            status = field.status or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            if status in ContextAssembler.ROUTING_REVIEW_STATUSES:
                unresolved_fields.append({
                    "field_name": field.field_name,
                    "status": status,
                    "value_present": bool(
                        (field.normalized_value_text or field.value_text or "").strip()
                    ),
                    "value_source": field.value_source,
                    "source_file_available": field.source_file_id is not None,
                    "evidence_excerpt_available": bool(
                        (field.source_excerpt or "").strip()
                    ),
                    "confidence_score": (
                        float(field.confidence_score)
                        if field.confidence_score is not None else None
                    ),
                })

        return {
            "field_status_counts": status_counts,
            "unresolved_field_count": len(unresolved_fields),
            "pic_supplied_fields": sorted(pic_supplied_fields),
            "vendor_response_received_fields": sorted(vendor_responses),
            "unresolved_fields": sorted(
                unresolved_fields, key=lambda item: item["field_name"]
            ),
        }

    @staticmethod
    def intake_classification(case_id: int) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()

        uploaded_files = json.dumps([
            {
                "id": f.id,
                "original_filename": f.original_filename,
                "file_type": f.file_type,
                "source_category": f.source_category,
                "upload_status": f.upload_status,
            }
            for f in files
        ], indent=2)

        email_content = "No email content provided"

        return {
            "uploaded_files": uploaded_files,
            "email_content": email_content,
        }

    @staticmethod
    def canonical_form(case_id: int) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()

        extracted_content = json.dumps([
            {
                "file_id": f.id,
                "filename": f.original_filename,
                "source_category": f.source_category,
                # OCR/parser output would be attached here in production
                "extracted_text_placeholder": f"Content from {f.original_filename}",
            }
            for f in files
        ], indent=2)

        form_fields_json = json.dumps([
            {
                "field_name": fv.field_name,
                "field_label": fv.field_label,
                "section_name": fv.section_name,
                "value_text": fv.value_text,
                "status": fv.status,
                "confidence_score": float(fv.confidence_score) if fv.confidence_score else None,
            }
            for fv in fields
        ], indent=2)

        return {
            "intake_classification": case.intake_type,
            "extracted_content": extracted_content,
            "email_content": "No email content provided",
            "current_form_fields": form_fields_json,
        }

    @staticmethod
    def verification(case_id: int) -> dict:
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()

        form_fields = json.dumps([
            {
                "field_name": fv.field_name,
                "field_label": fv.field_label,
                "value_text": fv.value_text,
                "normalized_value_text": fv.normalized_value_text,
                "status": fv.status,
                "follow_up_eligible": fv.field_name in FOLLOW_UP_FIELDS,
                "value_source": fv.value_source,
                "confidence_score": float(fv.confidence_score) if fv.confidence_score else None,
                "source_excerpt": fv.source_excerpt,
            }
            for fv in fields
        ], indent=2)

        mismatches = [
            fv for fv in fields
            if fv.status in ("mismatched_with_evidence", "conflicting", "unverifiable")
        ]
        alignment_report = json.dumps({
            "mismatched_fields": len(mismatches),
            "mismatches": [
                {"field_name": m.field_name, "status": m.status}
                for m in mismatches
                if m.field_name in FOLLOW_UP_FIELDS
            ],
        }, indent=2)
        internal_registry = InternalRegistryService.match_vendor(case_id)
        format_checks = ExternalVerificationService.verify_identifiers(
            case_id, internal_registry_match=internal_registry
        )

        return {
            "form_fields": form_fields,
            "alignment_report": alignment_report,
            "internal_registry": json.dumps(internal_registry, indent=2),
            "external_verification": json.dumps(format_checks, indent=2),
        }

    @staticmethod
    def policy_evaluation(case_id: int) -> dict:
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_values = {
            field.field_name: SafetyGate._field_value(field)
            for field in fields
        }
        follow_up_fields = [
            field for field in fields
            if field.field_name in FOLLOW_UP_FIELDS
            and (
                field.field_name not in {
                    "iso_certification_number",
                    "iso_certification_expiry_date",
                }
                or field_values.get("iso_certification_available", "")
                in SafetyGate.AFFIRMATIVE_VALUES
            )
        ]
        latest_verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()

        completeness = {
            "total_fields": len(follow_up_fields),
            "populated_fields": sum(
                1 for field in follow_up_fields if field_values.get(field.field_name)
            ),
            "missing_fields": sum(
                1 for field in follow_up_fields
                if not SafetyGate.has_pic_supplied_value(field)
                and (field.status == "missing" or not field_values.get(field.field_name))
            ),
            "conflicting_fields": sum(
                1 for field in follow_up_fields
                if not SafetyGate.has_pic_supplied_value(field)
                and field.status == "conflicting"
            ),
            "low_confidence_fields": sum(
                1 for field in follow_up_fields
                if not SafetyGate.has_pic_supplied_value(field)
                and field.status == "low_confidence"
            ),
            "vendor_response_received_fields": sorted(
                field.field_name for field in follow_up_fields
                if SafetyGate.has_vendor_response(field)
            ),
            "pic_supplied_fields": sorted(
                field.field_name for field in follow_up_fields
                if SafetyGate.has_pic_supplied_value(field)
                and not SafetyGate.has_vendor_response(field)
            ),
            "follow_up_scope_only": True,
        }
        completeness["completeness_percentage"] = (
            round(completeness["populated_fields"] / max(completeness["total_fields"], 1) * 100, 1)
        )

        vendor_profile = json.dumps({
            "intake_type": VendorCase.query.get(case_id).intake_type,
            "fields": [
                {
                    "field_name": fv.field_name,
                    "value_text": fv.value_text,
                    "status": fv.status,
                    "vendor_response_received": SafetyGate.has_vendor_response(fv),
                    "pic_supplied_value": SafetyGate.has_pic_supplied_value(fv),
                }
                for fv in fields
            ],
        }, indent=2)

        verification_report = "No verification run yet"
        if latest_verification:
            verification_report = json.dumps({
                "legitimacy_status": latest_verification.legitimacy_status,
                "mismatch_severity": latest_verification.mismatch_severity,
                "summary": latest_verification.summary,
                "key_findings": latest_verification.key_findings,
            }, indent=2)

        return {
            "completeness_report": json.dumps(completeness, indent=2),
            "verification_report": verification_report,
            "vendor_profile": vendor_profile,
        }

    @staticmethod
    def routing(case_id: int) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        latest_verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()
        latest_policy = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_values = {
            field.field_name: SafetyGate._field_value(field)
            for field in fields
        }
        follow_up_fields = [
            field for field in fields
            if field.field_name in FOLLOW_UP_FIELDS
            and (
                field.field_name not in {
                    "iso_certification_number",
                    "iso_certification_expiry_date",
                }
                or field_values.get("iso_certification_available", "")
                in SafetyGate.AFFIRMATIVE_VALUES
            )
        ]

        completeness = {
            "total_fields": len(follow_up_fields),
            "populated_fields": sum(
                1 for field in follow_up_fields if field_values.get(field.field_name)
            ),
            "missing_fields": sum(
                1 for field in follow_up_fields
                if field.status == "missing" or not field_values.get(field.field_name)
            ),
            "follow_up_scope_only": True,
        }

        verification_report = "No verification run"
        if latest_verification:
            verification_report = json.dumps({
                "legitimacy_status": latest_verification.legitimacy_status,
                "mismatch_severity": latest_verification.mismatch_severity,
                "summary": latest_verification.summary,
            }, indent=2)

        policy_report = "No policy evaluation run"
        if latest_policy:
            policy_report = json.dumps({
                "risk_band": latest_policy.risk_band,
                "blocking_issue_count": latest_policy.blocking_issue_count,
                "recoverable_issue_count": latest_policy.recoverable_issue_count,
                "policy_summary": latest_policy.policy_summary,
                "risk_flags": latest_policy.risk_flags,
            }, indent=2)

        field_evidence_report = json.dumps(
            ContextAssembler._routing_field_evidence(fields), indent=2
        )

        return {
            "completeness_report": json.dumps(completeness, indent=2),
            "verification_report": verification_report,
            "policy_report": policy_report,
            "field_evidence_report": field_evidence_report,
            "risk_band": latest_policy.risk_band if latest_policy else "unknown",
            "recoverable_issues": latest_policy.recoverable_issue_count if latest_policy else 0,
            "blocking_issues": latest_policy.blocking_issue_count if latest_policy else 0,
        }

    @staticmethod
    def follow_up(case_id: int) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        latest_routing = RoutingDecision.query.filter_by(
            vendor_case_id=case_id
        ).order_by(RoutingDecision.created_at.desc()).first()
        latest_policy = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()

        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        field_values = {
            field.field_name: SafetyGate._field_value(field)
            for field in fields
        }
        unresolved = [
            field for field in fields
            if SafetyGate.is_follow_up_needed(field, field_values)
        ]

        contact_fields = EyeVenFormFieldValue.query.filter(
            EyeVenFormFieldValue.vendor_case_id == case_id,
            EyeVenFormFieldValue.field_name.in_([
                "contact_email", "contact_person_name",
            ]),
        ).all()

        contact_map = {f.field_name: f.value_text for f in contact_fields}

        return {
            "routing_decision": json.dumps({
                "decision_code": latest_routing.decision_code if latest_routing else "unknown",
                "reasoning": latest_routing.decision_reasoning_summary if latest_routing else "",
            }, indent=2),
            "unresolved_fields": json.dumps([
                {
                    "field_name": f.field_name,
                    "field_label": f.field_label,
                    "status": f.status,
                    "value_text": f.value_text,
                }
                for f in unresolved
            ], indent=2),
            "recoverable_issues": latest_policy.recoverable_issue_count if latest_policy else 0,
            "evidence_conflicts": json.dumps([
                f.field_name for f in unresolved if f.status == "conflicting"
            ]),
            "vendor_contact": json.dumps(contact_map, indent=2),
        }
