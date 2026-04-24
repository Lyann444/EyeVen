from app.models import (
    VendorCase,
    VerificationRun,
    PolicyEvaluationRun,
    RoutingDecision,
    EmailDraft,
    ReprocessingRun,
)


class AuditService:
    @staticmethod
    def get_audit_trail(case_id):
        case = VendorCase.query.get_or_404(case_id)

        verifications = VerificationRun.query.filter_by(vendor_case_id=case_id).order_by(
            VerificationRun.created_at.asc()
        ).all()

        policies = PolicyEvaluationRun.query.filter_by(vendor_case_id=case_id).order_by(
            PolicyEvaluationRun.created_at.asc()
        ).all()

        routings = RoutingDecision.query.filter_by(vendor_case_id=case_id).order_by(
            RoutingDecision.created_at.asc()
        ).all()

        emails = EmailDraft.query.filter_by(vendor_case_id=case_id).order_by(
            EmailDraft.created_at.asc()
        ).all()

        reprocessings = ReprocessingRun.query.filter_by(vendor_case_id=case_id).order_by(
            ReprocessingRun.started_at.asc()
        ).all()

        return {
            "case_id": case_id,
            "case_reference": case.case_reference,
            "current_state": case.current_state,
            "verification_runs": [
                {
                    "id": v.id,
                    "legitimacy_status": v.legitimacy_status,
                    "mismatch_severity": v.mismatch_severity,
                    "summary": v.summary,
                    "confidence_score": float(v.confidence_score) if v.confidence_score else None,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in verifications
            ],
            "policy_runs": [
                {
                    "id": p.id,
                    "risk_band": p.risk_band,
                    "blocking_issue_count": p.blocking_issue_count,
                    "policy_summary": p.policy_summary,
                    "policy_confidence_score": float(p.policy_confidence_score) if p.policy_confidence_score else None,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in policies
            ],
            "routing_decisions": [
                {
                    "id": r.id,
                    "decision_code": r.decision_code,
                    "reasoning_summary": r.decision_reasoning_summary,
                    "confidence_score": float(r.confidence_score) if r.confidence_score else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in routings
            ],
            "email_drafts": [
                {
                    "id": e.id,
                    "draft_type": e.draft_type,
                    "subject_line": e.subject_line,
                    "draft_status": e.draft_status,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in emails
            ],
            "reprocessing_runs": [
                {
                    "id": rp.id,
                    "trigger_type": rp.trigger_type,
                    "previous_case_state": rp.previous_case_state,
                    "new_case_state": rp.new_case_state,
                    "run_status": rp.run_status,
                    "started_at": rp.started_at.isoformat() if rp.started_at else None,
                }
                for rp in reprocessings
            ],
        }

    @staticmethod
    def get_reprocessing_history(case_id):
        runs = ReprocessingRun.query.filter_by(vendor_case_id=case_id).order_by(
            ReprocessingRun.started_at.asc()
        ).all()

        return {
            "case_id": case_id,
            "reprocessing_runs": [
                {
                    "id": r.id,
                    "trigger_type": r.trigger_type,
                    "reason_summary": r.reason_summary,
                    "previous_case_state": r.previous_case_state,
                    "new_case_state": r.new_case_state,
                    "run_status": r.run_status,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in runs
            ],
        }
