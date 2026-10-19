import json

from app.models import (
    EyeVenFormFieldValue,
    PolicyEvaluationRun,
    VerificationRun,
    VendorCase,
)


class SafetyGate:
    """Deterministic veto for automatic vendor approval.

    Required-field rules below are explicit demo defaults, not a substitute
    for an organisation's approved procurement policy.
    """

    # Proposed baseline fields that must be populated before auto-approval.
    REQUIRED_FIELDS = {
        "company_registered_name",
        "company_registered_number",
        "company_registered_address",
        "business_type",
        "contact_person_name",
        "contact_email",
        "bank_name",
        "bank_account_name",
        "bank_account_number",
        "main_business_activities",
        "legal_disputes_past_5_years",
        "outsource_any_part_of_services",
        "authorized_signature_present",
        "signatory_name",
        "signatory_designation",
        "signatory_ic_or_passport_number",
        "declaration_date",
        "company_chop_present",
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
    ACCEPTABLE_EVIDENCE_STATUSES = {
        "matched_with_evidence",
        "filled_from_supporting_docs",
    }

    # Additional fields required only when the condition applies.
    CONDITIONAL_FIELDS = {
        "current_vendor_number": "re_registration",
        "business_type_other": "business_type_is_other",
        "iso_certification_number": "iso_certification_available",
        "iso_certification_expiry_date": "iso_certification_available",
        "legal_disputes_details": "legal_disputes_past_5_years",
        "outsourcing_details": "outsource_any_part_of_services",
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

        return SafetyGate.evaluate_results(
            verification,
            policy,
            fields,
            registration_type=case.registration_type,
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
        if condition == "re_registration":
            return registration_type == "re_registration"
        if condition == "business_type_is_other":
            return values.get("business_type", "") == "other"
        return values.get(condition, "") in SafetyGate.AFFIRMATIVE_VALUES

    @staticmethod
    def evaluate_results(
        verification, policy, fields, registration_type="new_registration"
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

        if registration_type not in ("new_registration", "re_registration"):
            recoverable_gaps.append("registration_type")

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
            ):
                review_blockers.append(
                    field_name + ":evidence_not_corroborated:" + str(status)
                )

        # Known contradictions or unreliable evidence block approval even when
        # the affected field is not in the required-field list.
        for field in fields:
            if field.status in SafetyGate.REVIEW_FIELD_STATUSES:
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
