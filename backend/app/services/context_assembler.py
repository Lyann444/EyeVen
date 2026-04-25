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
from app.services.cross_document_consistency_service import CrossDocumentConsistencyService


class ContextAssembler:
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

        CrossDocumentConsistencyService.update_field_statuses(case_id)
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()

        form_fields = json.dumps([
            {
                "field_name": fv.field_name,
                "field_label": fv.field_label,
                "value_text": fv.value_text,
                "normalized_value_text": fv.normalized_value_text,
                "status": fv.status,
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
            ],
        }, indent=2)

        cross_doc_report = CrossDocumentConsistencyService.check_consistency(case_id)

        # Check if any identity fields are conflicting across documents
        identity_fields = {"company_registered_name", "ssm_roc_rob_number", "company_registered_number"}
        has_identity_conflict = any(
            fr["field_name"] in identity_fields and fr["verdict"] == "severe"
            for fr in cross_doc_report.get("field_results", [])
        )

        conflicting_fields = [
            fv for fv in fields
            if fv.status in ("conflicting", "mismatched_with_evidence")
        ]

        registry_result = InternalRegistryService.match_vendor(case_id)

        # Only skip external verification if registry matches AND no identity
        # conflicts exist across documents. If documents disagree on the
        # company name/registration, a registry match on one value does not
        # mean the vendor is verified — the conflicting value must be
        # resolved first.
        if (
            registry_result.get("match_status") == "matched"
            and not has_identity_conflict
            and not conflicting_fields
        ):
            external_result = {
                "overall_status": "verified",
                "checks": {},
                "skipped_reason": "Vendor matched in internal registry; external verification not required",
            }
        else:
            external_result = ExternalVerificationService.verify_identifiers(case_id)
            if registry_result.get("match_status") == "matched" and (has_identity_conflict or conflicting_fields):
                # Registry matched on one value but documents disagree — flag it
                conflicting_names = [f.field_name for f in conflicting_fields]
                severe_fields = [
                    fr["field_name"] for fr in cross_doc_report.get("field_results", [])
                    if fr["field_name"] in identity_fields and fr["verdict"] == "severe"
                ]
                external_result["registry_conflict_warning"] = {
                    "registry_matched_as": registry_result.get("matched_vendor_number"),
                    "identity_conflicts": severe_fields or conflicting_names,
                    "detail": (
                        "Internal registry matched, but cross-document consistency "
                        "found conflicting identity fields. The matched registry entry "
                        "may correspond to a different vendor than the one in some documents."
                    ),
                }

        result = {
            "form_fields": form_fields,
            "alignment_report": alignment_report,
            "cross_document_consistency_report": json.dumps(cross_doc_report, indent=2),
            "internal_registry": json.dumps(registry_result, indent=2),
            "external_verification": json.dumps(external_result, indent=2),
        }

        # Surface blocking flags for downstream Policy/Routing agents
        if registry_result.get("match_status") == "rejected_high_risk_history":
            result["blocking_flags"] = {
                "high_risk_vendor": True,
                "reason": registry_result.get("reason", ""),
                "high_risk_reason": registry_result.get("high_risk_reason"),
            }

        return result

    @staticmethod
    def policy_evaluation(case_id: int) -> dict:
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
        latest_verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()

        completeness = {
            "total_fields": len(fields),
            "populated_fields": sum(1 for f in fields if f.value_text),
            "missing_fields": sum(1 for f in fields if f.status == "missing"),
            "conflicting_fields": sum(1 for f in fields if f.status == "conflicting"),
            "low_confidence_fields": sum(1 for f in fields if f.status == "low_confidence"),
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

        completeness = {
            "total_fields": len(fields),
            "populated_fields": sum(1 for f in fields if f.value_text),
            "missing_fields": sum(1 for f in fields if f.status == "missing"),
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

        return {
            "completeness_report": json.dumps(completeness, indent=2),
            "verification_report": verification_report,
            "policy_report": policy_report,
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

        unresolved = EyeVenFormFieldValue.query.filter(
            EyeVenFormFieldValue.vendor_case_id == case_id,
            EyeVenFormFieldValue.status.in_([
                "missing", "conflicting", "unverifiable",
                "low_confidence", "declaration_missing", "mismatched_with_evidence",
            ]),
        ).all()

        contact_fields = EyeVenFormFieldValue.query.filter(
            EyeVenFormFieldValue.vendor_case_id == case_id,
            EyeVenFormFieldValue.field_name.in_([
                "contact_email", "contact_person_name", "company_email",
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
