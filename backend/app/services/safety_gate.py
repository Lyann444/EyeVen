from app.models import (
    EyeVenFormFieldValue,
    PolicyEvaluationRun,
    VerificationRun,
    VendorCase,
)


class SafetyGate:
    """Deterministic veto for automatic vendor approval.

    This gate does not decide that a vendor is legitimate. It only determines
    whether persisted results are sufficient to allow automatic approval.
    """

    RECOVERABLE_FIELD_STATUSES = {"missing", "declaration_missing"}
    REVIEW_FIELD_STATUSES = {
        "conflicting",
        "unverifiable",
        "low_confidence",
        "mismatched_with_evidence",
    }

    @staticmethod
    def evaluate(case_id: int) -> dict:
        """Load persisted case evidence and evaluate the approval rules."""
        VendorCase.query.get_or_404(case_id)
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

        return SafetyGate.evaluate_results(verification, policy, fields)

    @staticmethod
    def evaluate_results(verification, policy, fields) -> dict:
        """Pure rule evaluation separated from database access for unit tests."""
        reasons = []
        recoverable_gaps = []
        review_blockers = []

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

        if verification is not None and verification.mismatch_severity in (
            "moderate",
            "critical",
        ):
            reasons.append(
                "mismatch_severity_not_eligible_for_auto_approval:"
                + str(verification.mismatch_severity)
            )

        if policy is None:
            reasons.append("policy_result_missing")
        else:
            if policy.risk_band != "low":
                reasons.append("policy_risk_not_low:" + str(policy.risk_band))
            if policy.blocking_issue_count != 0:
                reasons.append("policy_blocking_issues_present")

        if not fields:
            reasons.append("no_form_field_evidence_available")

        for field in fields:
            if field.status in SafetyGate.RECOVERABLE_FIELD_STATUSES:
                recoverable_gaps.append(field.field_name)
            elif field.status in SafetyGate.REVIEW_FIELD_STATUSES:
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
            "recoverable_fields": recoverable_gaps,
            "review_blockers": review_blockers,
        }
