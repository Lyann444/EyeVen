from app.extensions import db
from app.models import EmailDraft, VendorCase
from app.services.internal_registry_service import InternalRegistryService
from app.services.next_action_planner import (
    NextActionPlanner,
    NextActionPlannerError,
    TARGETABLE_FIELDS,
    SUPPORTED_STAGES,
)


class ActionExecutorError(Exception):
    pass


class ActionExecutor:
    @staticmethod
    def execute(case_id: int, plan: dict) -> dict:
        try:
            plan = NextActionPlanner.validate_plan(plan)
        except NextActionPlannerError as error:
            raise ActionExecutorError(str(error)) from error

        action = plan["action"]
        case = VendorCase.query.get_or_404(case_id)
        if (
            action in {
                "reprocess_stage",
                "request_pic_upload",
                "request_information",
                "manual_review",
            }
            and case.current_state in {"completed", "rejected", "flagged_high_risk"}
        ):
            return {
                "status": "blocked_terminal_case",
                "current_state": case.current_state,
            }
        if action == "targeted_verification":
            return ActionExecutor._targeted_verification(
                case_id, plan["target_fields"]
            )
        if action == "reprocess_stage":
            return ActionExecutor._reprocess_stage(case_id, plan["target_stage"])
        if action == "request_pic_upload":
            return ActionExecutor._request_pic_upload(case_id)
        if action == "request_information":
            return ActionExecutor._request_information(case_id)
        if action == "manual_review":
            return ActionExecutor._manual_review(case_id, plan["reason"])
        if action == "finish":
            return {"status": "finished"}
        raise ActionExecutorError(f"Unsupported planner action: {action!r}")

    @staticmethod
    def _request_pic_upload(case_id: int) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        if case.current_state in {"completed", "rejected", "flagged_high_risk"}:
            return {
                "status": "blocked_terminal_case",
                "current_state": case.current_state,
            }
        if case.current_state != "awaiting_pic_upload":
            case.current_state = "awaiting_pic_upload"
            db.session.commit()
        return {
            "status": "awaiting_pic_upload",
            "current_state": case.current_state,
        }

    @staticmethod
    def _targeted_verification(case_id: int, target_fields: list[str]) -> dict:
        if not target_fields or not set(target_fields).issubset(TARGETABLE_FIELDS):
            raise ActionExecutorError("Unsupported internal-registry target fields")

        result = InternalRegistryService.match_vendor(
            case_id, target_fields=target_fields
        )
        return {
            "status": "completed",
            "source": "internal_demo_registry",
            "match_status": result["match_status"],
            "matched_vendor_number": result.get("matched_vendor_number"),
            "target_fields": target_fields,
            "external_verification_performed": False,
        }

    @staticmethod
    def _reprocess_stage(case_id: int, target_stage: str) -> dict:
        if target_stage not in SUPPORTED_STAGES:
            raise ActionExecutorError(f"Unsupported reprocessing stage: {target_stage!r}")

        from app.orchestrators.reprocessing_orchestrator import ReprocessingOrchestrator

        result = ReprocessingOrchestrator.run_stage(case_id, target_stage)
        return {
            "status": result.get("status"),
            "stage": result.get("stage"),
            "downstream_stages": result.get("downstream_stages", []),
            "current_state": result.get("new_state"),
            "error": result.get("error"),
            "reprocessing_run_id": result.get("reprocessing_run_id"),
        }

    @staticmethod
    def _request_information(case_id: int) -> dict:
        existing_draft = EmailDraft.query.filter_by(
            vendor_case_id=case_id
        ).first()
        if existing_draft:
            return {
                "status": "already_available",
                "email_draft_id": existing_draft.id,
            }

        from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator

        result = WorkflowOrchestrator._run_follow_up(case_id)
        if result.get("status") != "glm_failed":
            case = VendorCase.query.get(case_id)
            if case.current_state not in {
                "completed", "rejected", "flagged_high_risk",
            }:
                case.current_state = "pending_follow_up"
                db.session.commit()
        return {
            "status": result.get("status", "completed"),
            "email_draft_id": result.get("email_draft_id"),
        }

    @staticmethod
    def _manual_review(case_id: int, reason: str) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        if case.current_state in {"completed", "rejected", "flagged_high_risk"}:
            return {"status": "blocked_terminal_case", "current_state": case.current_state}
        case.current_state = "manual_review_required"
        db.session.commit()
        return {
            "status": "completed",
            "current_state": case.current_state,
            "reason": reason,
        }
