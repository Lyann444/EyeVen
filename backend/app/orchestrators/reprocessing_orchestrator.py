import logging
from datetime import datetime

from app.extensions import db
from app.models import (
    VendorCase,
    UploadedFile,
    EyeVenFormFieldValue,
    VerificationRun,
    PolicyEvaluationRun,
    RoutingDecision,
    EmailDraft,
    ReprocessingRun,
)
from app.orchestrators.extraction_orchestrator import ExtractionOrchestrator
from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)


class ReprocessingOrchestrator:
    @staticmethod
    def run(case_id, trigger_type="corrected_upload"):
        case = VendorCase.query.get_or_404(case_id)
        previous_state = case.current_state

        reprocessing_run = ReprocessingRun(
            vendor_case_id=case_id,
            trigger_type=trigger_type,
            previous_case_state=previous_state,
            run_status="started",
        )
        db.session.add(reprocessing_run)
        case.current_state = "reprocessing"
        db.session.commit()

        try:
            ExtractionOrchestrator.run(case_id)
            case = VendorCase.query.get(case_id)

            workflow_result = WorkflowOrchestrator.run(case_id)
            case = VendorCase.query.get(case_id)

            reprocessing_run.new_case_state = case.current_state
            reprocessing_run.run_status = "completed"
            reprocessing_run.completed_at = datetime.utcnow()

            diff = ReprocessingOrchestrator._compute_diff(case_id, reprocessing_run.id)

            db.session.commit()

            return {
                "case_id": case_id,
                "reprocessing_run_id": reprocessing_run.id,
                "previous_state": previous_state,
                "new_state": case.current_state,
                "status": "completed",
                "diff": diff,
                "workflow_result": workflow_result,
            }

        except Exception as e:
            logger.error("Reprocessing failed for case %d: %s", case_id, e)
            reprocessing_run.run_status = "failed"
            reprocessing_run.new_case_state = case.current_state
            reprocessing_run.completed_at = datetime.utcnow()
            case.current_state = "manual_review_required"
            db.session.commit()

            return {
                "case_id": case_id,
                "reprocessing_run_id": reprocessing_run.id,
                "previous_state": previous_state,
                "status": "failed",
                "error": str(e),
            }

    @staticmethod
    def _compute_diff(case_id, reprocessing_run_id):
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()

        resolved = [f for f in fields if f.value_text and f.status in (
            "provided_in_form", "matched_with_evidence", "filled_from_supporting_docs"
        )]
        remaining_missing = [f for f in fields if f.status == "missing"]
        remaining_conflicting = [f for f in fields if f.status == "conflicting"]

        prev_verification = VerificationRun.query.filter(
            VerificationRun.vendor_case_id == case_id,
            VerificationRun.reprocessing_run_id != reprocessing_run_id,
        ).order_by(VerificationRun.created_at.desc()).first()

        curr_verification = VerificationRun.query.filter(
            VerificationRun.vendor_case_id == case_id,
        ).order_by(VerificationRun.created_at.desc()).first()

        prev_routing = RoutingDecision.query.filter(
            RoutingDecision.vendor_case_id == case_id,
            RoutingDecision.reprocessing_run_id != reprocessing_run_id,
        ).order_by(RoutingDecision.created_at.desc()).first()

        curr_routing = RoutingDecision.query.filter(
            RoutingDecision.vendor_case_id == case_id,
        ).order_by(RoutingDecision.created_at.desc()).first()

        return {
            "resolved_fields": [f.field_name for f in resolved],
            "resolved_count": len(resolved),
            "remaining_missing": [f.field_name for f in remaining_missing],
            "remaining_missing_count": len(remaining_missing),
            "remaining_conflicting": [f.field_name for f in remaining_conflicting],
            "remaining_conflicting_count": len(remaining_conflicting),
            "verification_change": {
                "previous": prev_verification.legitimacy_status if prev_verification else None,
                "current": curr_verification.legitimacy_status if curr_verification else None,
            } if prev_verification and curr_verification else None,
            "routing_change": {
                "previous": prev_routing.decision_code if prev_routing else None,
                "current": curr_routing.decision_code if curr_routing else None,
            } if prev_routing and curr_routing else None,
        }
