import json

from app.models import (
    EyeVenFormFieldValue,
    PolicyEvaluationRun,
    VerificationRun,
    VendorCase,
)
from app.services.internal_registry_service import InternalRegistryService


FOLLOW_UP_FIELDS = {
    "company_registered_name",
    "company_registered_number",
    "company_website",
    "main_office_phone_number",
    "bank_account_name",
    "bank_account_number",
    "bank_name",
    "latest_annual_revenue_range",
    "contact_email",
    "contact_mobile_number",
    "contact_person_name",
    "iso_certification_available",
    "iso_certification_expiry_date",
    "iso_certification_number",
    "ssm_roc_rob_number",
    "tax_sst_gst_number",
}


class SafetyGate:
    """Deterministic veto for automatic vendor approval.

    Required-field rules below are explicit demo defaults, not a substitute
    for an organisation's approved procurement policy.
    """

    # Proposed baseline fields that must be populated before auto-approval.
    REQUIRED_FIELDS = {
        "company_registered_name",
        "company_registered_number",
        "company_website",
        "main_office_phone_number",
        "bank_account_number",
        "bank_account_name",
        "bank_name",
        "latest_annual_revenue_range",
        "contact_email",
        "contact_mobile_number",
        "contact_person_name",
        "iso_certification_available",
        "ssm_roc_rob_number",
        "tax_sst_gst_number",
    }

    # High-impact identity and payment fields need corroboration, not merely
    # a value copied from the submitted form.
    EVIDENCE_REQUIRED_FIELDS = {
        "company_registered_name",
        "company_registered_number",
        "company_registered_address",
        "bank_name",
        "bank_account_name",
        "bank_account_number",
    }
    BANK_FIELDS = {
        "bank_name",
        "bank_account_name",
        "bank_account_number",
    }
    ACCEPTABLE_EVIDENCE_STATUSES = {
        "matched_with_evidence",
        "filled_from_supporting_docs",
    }

    # Certification details are requested only when the vendor claims ISO certification.
    CONDITIONAL_FIELDS = {
        "iso_certification_number": "iso_certification_available",
        "iso_certification_expiry_date": "iso_certification_available",
    }

    RECOVERABLE_FIELD_STATUSES = {"missing", "declaration_missing"}
    REVIEW_FIELD_STATUSES = {
        "conflicting",
        "unverifiable",
        "low_confidence",
        "mismatched_with_evidence",
    }
    AFFIRMATIVE_VALUES = {"yes", "y", "true", "1"}

    @staticmethod
    def has_vendor_response(field) -> bool:
        return (
            "Vendor response received and transcribed by PIC."
            in (getattr(field, "notes", None) or "")
            and bool(SafetyGate._field_value(field))
        )

    @staticmethod
    def has_pic_supplied_value(field) -> bool:
        notes = getattr(field, "notes", None) or ""
        return (
            bool(SafetyGate._field_value(field))
            and (
                SafetyGate.has_vendor_response(field)
                or "PIC manual correction; not independently verified." in notes
            )
        )

    @staticmethod
    def evaluate(case_id: int) -> dict:
        """Load persisted case evidence and evaluate the approval rules."""
        case = VendorCase.query.get_or_404(case_id)
        verification = (
            VerificationRun.query.filter_by(vendor_case_id=case_id)
            .order_by(VerificationRun.created_at.desc())
            .first()
        )
        policy = (
            PolicyEvaluationRun.query.filter_by(vendor_case_id=case_id)
            .order_by(PolicyEvaluationRun.created_at.desc())
            .first()
        )
        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        registry_match = InternalRegistryService.match_vendor(case_id)

        return SafetyGate.evaluate_results(
            verification,
            policy,
            fields,
            registration_type=case.registration_type,
            registry_match=registry_match,
        )

    @staticmethod
    def _field_value(field) -> str:
        """Prefer normalized values, falling back to the extracted value."""
        value = getattr(field, "normalized_value_text", None)
        if value is None or not str(value).strip():
            value = getattr(field, "value_text", None)
        return str(value).strip().lower() if value is not None else ""

    @staticmethod
    def _condition_applies(condition, values, registration_type) -> bool:
        return values.get(condition, "") in SafetyGate.AFFIRMATIVE_VALUES

    @staticmethod
    def is_in_scope_field(field, values=None) -> bool:
        if field.field_name not in FOLLOW_UP_FIELDS:
            return False
        condition = SafetyGate.CONDITIONAL_FIELDS.get(field.field_name)
        return condition is None or SafetyGate._condition_applies(
            condition, values or {}, None
        )

    @staticmethod
    def is_follow_up_needed(field, values=None) -> bool:
        values = values or {}
        if not SafetyGate.is_in_scope_field(field, values):
            return False
        if SafetyGate.has_pic_supplied_value(field):
            return False
        unresolved_statuses = (
            SafetyGate.RECOVERABLE_FIELD_STATUSES
            | SafetyGate.REVIEW_FIELD_STATUSES
        )
        return (
            getattr(field, "status", None) in unresolved_statuses
            or not SafetyGate._field_value(field)
        )

    @staticmethod
    def evaluate_results(
        verification,
        policy,
        fields,
        registration_type="new_registration",
        registry_match=None,
    ) -> dict:
        """Pure rule evaluation separated from database access for unit tests."""
        reasons = []
        recoverable_gaps = []
        review_blockers = []
        fields = fields or []
        field_by_name = {field.field_name: field for field in fields}
        values = {
            name: SafetyGate._field_value(field)
            for name, field in field_by_name.items()
        }
        registry_corroborated_fields = SafetyGate._registry_corroborated_fields(
            registry_match
        )

        if verification is None or verification.run_status != "completed":
            reasons.append("verification_result_missing_or_incomplete")
        elif verification.legitimacy_status not in (
            "verified",
            "verified_with_minor_gaps",
        ):
            reasons.append(
                "verification_not_acceptable:"
                + str(verification.legitimacy_status)
            )

        # Missing severity is also unknown, so fail closed rather than treating
        # absent mismatch information as evidence that there are no mismatches.
        if verification is not None:
            if verification.mismatch_severity in ("moderate", "critical"):
                reasons.append(
                    "mismatch_severity_not_eligible_for_auto_approval:"
                    + str(verification.mismatch_severity)
                )
            elif verification.mismatch_severity not in ("minor",):
                reasons.append("mismatch_severity_missing_or_unknown")

        if policy is None:
            reasons.append("policy_result_missing")
        else:
            if policy.risk_band != "low":
                reasons.append("policy_risk_not_low:" + str(policy.risk_band))
            if policy.blocking_issue_count != 0:
                reasons.append("policy_blocking_issues_present")

            # Cross-check the count against persisted risk flags so an
            # inconsistent model output cannot hide a blocking flag.
            raw_risk_flags = getattr(policy, "risk_flags", "[]")
            try:
                risk_flags = (
                    json.loads(raw_risk_flags)
                    if isinstance(raw_risk_flags, str)
                    else raw_risk_flags
                )
                if not isinstance(risk_flags, list):
                    raise ValueError("risk_flags must be a list")
                if any(
                    isinstance(flag, dict) and flag.get("severity") == "blocking"
                    for flag in risk_flags
                ):
                    reasons.append("blocking_risk_flag_present")
            except (TypeError, ValueError, json.JSONDecodeError):
                reasons.append("policy_risk_flags_malformed")

        if not fields:
            reasons.append("no_form_field_evidence_available")

        required_fields = set(SafetyGate.REQUIRED_FIELDS)
        required_fields.update(
            field_name
            for field_name, condition in SafetyGate.CONDITIONAL_FIELDS.items()
            if SafetyGate._condition_applies(condition, values, registration_type)
        )

        # Missing optional fields do not block approval. Only fields explicitly
        # listed as required under the applicable conditions are checked here.
        for field_name in sorted(required_fields):
            field = field_by_name.get(field_name)
            if field is None:
                recoverable_gaps.append(field_name)
                continue

            status = getattr(field, "status", None)
            value = SafetyGate._field_value(field)
            if status in SafetyGate.RECOVERABLE_FIELD_STATUSES or not value:
                recoverable_gaps.append(field_name)

            if (
                field_name in SafetyGate.EVIDENCE_REQUIRED_FIELDS
                and value
                and status not in SafetyGate.ACCEPTABLE_EVIDENCE_STATUSES
                and field_name not in registry_corroborated_fields
                and not (
                    field_name in SafetyGate.BANK_FIELDS
                    and isinstance(registry_match, dict)
                    and registry_match.get("match_status") == "matched"
                )
            ):
                review_blockers.append(
                    field_name + ":evidence_not_corroborated:" + str(status)
                )

        # Known contradictions or unreliable evidence block approval even when
        # the affected field is not in the required-field list.
        for field in fields:
            if not SafetyGate.is_in_scope_field(field, values):
                continue
            notes = getattr(field, "notes", None) or ""
            manually_corrected = (
                "PIC manual correction; not independently verified." in notes
            )
            if (
                manually_corrected
                and field.status not in SafetyGate.ACCEPTABLE_EVIDENCE_STATUSES
                and field.field_name not in registry_corroborated_fields
            ):
                review_blockers.append(
                    field.field_name + ":manual_correction_not_independently_verified"
                )

            if (
                (
                    SafetyGate.is_follow_up_needed(field, values)
                    or SafetyGate.has_pic_supplied_value(field)
                )
                and field.status in SafetyGate.REVIEW_FIELD_STATUSES
            ):
                review_blockers.append(
                    field.field_name + ":" + str(field.status)
                )

        if recoverable_gaps:
            reasons.append("unresolved_required_information")
        if review_blockers:
            reasons.append("unresolved_evidence_or_confidence_issues")

        allowed = not reasons
        if allowed:
            suggested_decision = "complete_verified"
        elif (
            recoverable_gaps
            and not review_blockers
            and all(
                reason == "unresolved_required_information"
                for reason in reasons
            )
        ):
            suggested_decision = "follow_up_required"
        elif policy is not None and policy.risk_band in ("high", "critical"):
            suggested_decision = "flagged_high_risk"
        else:
            suggested_decision = "manual_review_required"

        return {
            "allowed": allowed,
            "suggested_decision": suggested_decision,
            "reasons": reasons,
            "recoverable_fields": sorted(set(recoverable_gaps)),
            "review_blockers": review_blockers,
        }

    @staticmethod
    def _registry_corroborated_fields(registry_match):
        if not isinstance(registry_match, dict) or registry_match.get("match_status") != "matched":
            return set()

        matched_vendor_number = registry_match.get("matched_vendor_number")
        for comparison in registry_match.get("field_comparisons", []):
            if comparison.get("vendor_number") != matched_vendor_number:
                continue
            return {
                item.get("field_name")
                for item in comparison.get("field_results", [])
                if item.get("match") == "exact"
                and item.get("field_name") in SafetyGate.EVIDENCE_REQUIRED_FIELDS
            }
        return set()
