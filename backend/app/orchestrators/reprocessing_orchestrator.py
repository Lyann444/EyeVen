import logging
import hashlib
import json
from datetime import datetime

from app.extensions import db
from app.orchestrators.workflow_lock import prevent_concurrent_case_runs
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
from app.services.safety_gate import FOLLOW_UP_FIELDS, SafetyGate

logger = logging.getLogger(__name__)


class ReprocessingOrchestrator:
    SUPPORTED_STAGES = {
        "extraction",
        "form_structuring",
        "verification",
        "policy_evaluation",
        "routing",
    }
    SUPPORTED_REASONS = {
        "new_document_uploaded",
        "manual_correction",
        "new_vendor_information",
        "stale_downstream_results",
        "recoverable_failure",
        "automatic_retry",
    }
    DOWNSTREAM_STAGES = {
        "extraction": ["form_structuring", "verification", "policy_evaluation", "routing"],
        "form_structuring": ["verification", "policy_evaluation", "routing"],
        "verification": ["policy_evaluation", "routing"],
        "policy_evaluation": ["routing"],
        "routing": [],
    }

    @staticmethod
    @prevent_concurrent_case_runs
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
    @prevent_concurrent_case_runs
    def run_stage(case_id, target_stage):
        return ReprocessingOrchestrator.reprocess(
            case_id, target_stage, "recoverable_failure"
        )

    @staticmethod
    @prevent_concurrent_case_runs
    def reprocess(case_id, stage, reason):
        if stage not in ReprocessingOrchestrator.SUPPORTED_STAGES:
            raise ValueError(f"Unsupported reprocessing stage: {stage!r}")
        if reason not in ReprocessingOrchestrator.SUPPORTED_REASONS:
            raise ValueError(f"Unsupported reprocessing reason: {reason!r}")
        case = VendorCase.query.get_or_404(case_id)
        ReprocessingOrchestrator._validate_prerequisites(case_id, stage)
        input_fingerprint = ReprocessingOrchestrator._input_fingerprint(case_id, stage)
        if (
            reason == "automatic_retry"
            and ReprocessingOrchestrator._automatic_retry_attempted(
                case_id, stage, input_fingerprint
            )
        ):
            return {
                "action": "reprocess_stage",
                "case_id": case_id,
                "stage": stage,
                "reason": reason,
                "status": "retry_limit_reached",
                "result_changed": False,
                "downstream_stages_invalidated": [],
                "downstream_stages_rerun": [],
                "error": None,
            }
        if reason != "recoverable_failure":
            previous_success = ReprocessingRun.query.filter_by(
                vendor_case_id=case_id,
                run_status="completed",
            ).order_by(ReprocessingRun.started_at.desc()).all()
            if any(
                ReprocessingOrchestrator._run_metadata(run).get("stage") == stage
                and ReprocessingOrchestrator._run_metadata(run).get("input_fingerprint")
                == input_fingerprint
                for run in previous_success
            ):
                unresolved = ReprocessingOrchestrator._unresolved_issues(case_id)
                return {
                    "action": "reprocess_stage",
                    "case_id": case_id,
                    "stage": stage,
                    "reason": reason,
                    "status": "skipped_unchanged",
                    "result_changed": False,
                    "downstream_stages_invalidated": [],
                    "downstream_stages_rerun": [],
                    "unresolved_issues": unresolved,
                    "requires_further_processing": bool(unresolved),
                    "error": None,
                }

        previous_state = case.current_state
        reprocessing_run = ReprocessingRun(
            vendor_case_id=case_id,
            trigger_type="manual_rerun",
            reason_summary=json.dumps({
                "stage": stage,
                "reason": reason,
                "input_fingerprint": input_fingerprint,
                "attempt": 1 if reason == "automatic_retry" else None,
            }),
            previous_case_state=previous_state,
            run_status="started",
        )
        db.session.add(reprocessing_run)
        case.current_state = "reprocessing"
        db.session.commit()
        before_result = ReprocessingOrchestrator._result_fingerprint(case_id)
        stage_result = None

        try:
            from app.services.form_service import FormService

            if stage == "extraction":
                stage_result = WorkflowOrchestrator._summarize_extraction_result(
                    ExtractionOrchestrator.run(case_id)
                )
            elif stage == "form_structuring":
                stage_result = FormService.structure_form(case_id)
            elif stage == "verification":
                stage_result = WorkflowOrchestrator._run_verification(case_id)
            elif stage == "policy_evaluation":
                stage_result = WorkflowOrchestrator._run_policy_evaluation(case_id)
            else:
                case.current_state = "routing_in_progress"
                db.session.commit()
                stage_result = WorkflowOrchestrator._run_routing(case_id)

            if stage_result.get("status") in {"failed", "glm_failed"}:
                case = VendorCase.query.get(case_id)
                case.current_state = "manual_review_required"
                ReprocessingOrchestrator._update_run_metadata(
                    reprocessing_run,
                    result_status=stage_result.get("status"),
                    retryable=stage_result.get("retryable", False),
                )
                reprocessing_run.run_status = "failed"
                reprocessing_run.new_case_state = case.current_state
                reprocessing_run.completed_at = datetime.utcnow()
                db.session.commit()
                return {
                    "case_id": case_id,
                    "reprocessing_run_id": reprocessing_run.id,
                    "status": "failed",
                    "stage": stage,
                    "new_state": case.current_state,
                    "reason": reason,
                    "result_changed": False,
                    "downstream_stages_invalidated": ReprocessingOrchestrator.DOWNSTREAM_STAGES[stage],
                    "downstream_stages_rerun": [],
                    "unresolved_issues": ReprocessingOrchestrator._unresolved_issues(case_id),
                    "requires_further_processing": True,
                    "stage_result_summary": (
                        ReprocessingOrchestrator._stage_result_summary(stage_result)
                    ),
                }

            workflow_result = (
                {"stages_run": ["routing"]}
                if stage == "routing"
                else WorkflowOrchestrator.run(case_id, include_agentic=False)
            )
            case = VendorCase.query.get(case_id)
            ReprocessingOrchestrator._update_run_metadata(
                reprocessing_run,
                result_status=stage_result.get("status", "completed"),
                retryable=stage_result.get("retryable", False),
            )
            reprocessing_run.new_case_state = case.current_state
            reprocessing_run.run_status = "completed"
            reprocessing_run.completed_at = datetime.utcnow()
            db.session.commit()
            after_result = ReprocessingOrchestrator._result_fingerprint(case_id)
            unresolved = ReprocessingOrchestrator._unresolved_issues(case_id)
            downstream_rerun = workflow_result.get("stages_run", [])
            return {
                "action": "reprocess_stage",
                "case_id": case_id,
                "reprocessing_run_id": reprocessing_run.id,
                "status": "completed",
                "stage": stage,
                "new_state": case.current_state,
                "reason": reason,
                "result_changed": before_result != after_result,
                "downstream_stages_invalidated": ReprocessingOrchestrator.DOWNSTREAM_STAGES[stage],
                "downstream_stages_rerun": downstream_rerun,
                "downstream_stages": downstream_rerun,
                "unresolved_issues": unresolved,
                "requires_further_processing": bool(unresolved),
                "workflow_result": workflow_result,
                "error": None,
                "stage_result_summary": (
                    ReprocessingOrchestrator._stage_result_summary(stage_result)
                ),
            }
        except Exception as error:
            logger.exception(
                "Stage reprocessing failed for case %d at %s",
                case_id,
                stage,
            )
            case = VendorCase.query.get(case_id)
            ReprocessingOrchestrator._update_run_metadata(
                reprocessing_run,
                result_status="failed",
                error_type=type(error).__name__,
            )
            case.current_state = "manual_review_required"
            reprocessing_run.run_status = "failed"
            reprocessing_run.new_case_state = case.current_state
            reprocessing_run.completed_at = datetime.utcnow()
            db.session.commit()
            return {
                "case_id": case_id,
                "reprocessing_run_id": reprocessing_run.id,
                "status": "failed",
                "stage": stage,
                "new_state": case.current_state,
                "reason": reason,
                "result_changed": False,
                "downstream_stages_invalidated": ReprocessingOrchestrator.DOWNSTREAM_STAGES[stage],
                "downstream_stages_rerun": [],
                "unresolved_issues": ReprocessingOrchestrator._unresolved_issues(case_id),
                "requires_further_processing": True,
                "error": str(error),
                "stage_result_summary": (
                    ReprocessingOrchestrator._stage_result_summary(stage_result)
                    if stage_result is not None
                    else None
                ),
            }

    @staticmethod
    def _validate_prerequisites(case_id, stage):
        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()
        verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()
        policy = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()

        if stage in {"extraction", "form_structuring"} and not files:
            raise ValueError(f"{stage} requires at least one uploaded document")
        if stage in {"verification", "policy_evaluation", "routing"} and not fields:
            raise ValueError(f"{stage} requires structured form fields")
        if stage in {"policy_evaluation", "routing"} and not verification:
            raise ValueError(f"{stage} requires a completed verification result")
        if stage == "routing" and not policy:
            raise ValueError("routing requires a completed policy evaluation")

    @staticmethod
    def _run_metadata(run):
        try:
            metadata = json.loads(getattr(run, "reason_summary", None) or "{}")
            return metadata if isinstance(metadata, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _update_run_metadata(run, **updates):
        metadata = ReprocessingOrchestrator._run_metadata(run)
        metadata.update(updates)
        run.reason_summary = json.dumps(metadata)

    @staticmethod
    def _stage_result_summary(result):
        if not isinstance(result, dict):
            return {"status": "failed"}
        allowed_keys = {
            "status",
            "retryable",
            "failures",
            "technical_failures",
            "ocr_warnings",
        }
        return {key: value for key, value in result.items() if key in allowed_keys}

    @staticmethod
    def _automatic_retry_attempted(case_id, stage, input_fingerprint):
        runs = ReprocessingRun.query.filter_by(vendor_case_id=case_id).all()
        return any(
            (metadata := ReprocessingOrchestrator._run_metadata(run)).get("reason")
            == "automatic_retry"
            and metadata.get("stage") == stage
            and metadata.get("input_fingerprint") == input_fingerprint
            for run in runs
        )

    @staticmethod
    def _input_fingerprint(case_id, stage):
        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).order_by(EyeVenFormFieldValue.field_name.asc()).all()
        files = UploadedFile.query.filter_by(
            vendor_case_id=case_id
        ).order_by(UploadedFile.id.asc()).all()
        payload = {
            "stage": stage,
            "fields": [
                (
                    item.field_name,
                    item.value_text,
                    item.normalized_value_text,
                    item.status,
                    item.notes,
                )
                for item in fields
            ] if stage not in {"extraction", "form_structuring"} else [],
            "files": [
                (item.id, item.checksum_sha256, item.upload_status)
                for item in files
            ] if stage in {"extraction", "form_structuring"} else [],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _result_fingerprint(case_id):
        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).order_by(EyeVenFormFieldValue.field_name.asc()).all()
        verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()
        policy = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()
        routing = RoutingDecision.query.filter_by(
            vendor_case_id=case_id
        ).order_by(RoutingDecision.created_at.desc()).first()
        payload = {
            "fields": [
                (item.field_name, item.value_text, item.normalized_value_text, item.status)
                for item in fields
            ],
            "verification": (
                verification.legitimacy_status,
                verification.mismatch_severity,
                verification.summary,
            ) if verification else None,
            "policy": (
                policy.risk_band,
                policy.blocking_issue_count,
                policy.recoverable_issue_count,
                policy.policy_summary,
            ) if policy else None,
            "routing": (
                routing.decision_code,
                routing.decision_reasoning_summary,
            ) if routing else None,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _unresolved_issues(case_id):
        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        values = {field.field_name: SafetyGate._field_value(field) for field in fields}
        return [
            field.field_name
            for field in fields
            if SafetyGate.is_follow_up_needed(field, values)
        ]

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
