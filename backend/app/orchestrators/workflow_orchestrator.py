import json
import logging
import math
import time
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.orchestrators.workflow_lock import prevent_concurrent_case_runs
from app.models import (
    VendorCase,
    EyeVenFormFieldValue,
    VerificationRun,
    PolicyEvaluationRun,
    RoutingDecision,
    EmailDraft,
    UploadedFile,
)
from app.orchestrators.extraction_orchestrator import ExtractionOrchestrator
from app.services.form_service import FormService
from app.services.glm_service import GlmService, GlmServiceError
from app.services.context_assembler import ContextAssembler
from app.services.safety_gate import FOLLOW_UP_FIELDS, SafetyGate
from app.services.action_executor import ActionExecutor, ActionExecutorError
from app.services.next_action_planner import (
    NextActionPlanner,
    NextActionPlannerError,
)
from app.prompts.verification import VERIFICATION_PROMPT
from app.prompts.policy_evaluation import POLICY_EVALUATION_PROMPT
from app.prompts.routing import ROUTING_PROMPT
from app.prompts.follow_up import FOLLOW_UP_PROMPT
from app.validators.agent_schemas import (
    CANONICAL_FIELD_NAMES,
    VERIFICATION_SCHEMA,
    POLICY_EVALUATION_SCHEMA,
    ROUTING_SCHEMA,
    FOLLOW_UP_SCHEMA,
)

logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    @staticmethod
    @prevent_concurrent_case_runs
    def run(case_id, include_agentic=True):
        case = VendorCase.query.get_or_404(case_id)

        if case.current_state in ("completed", "rejected", "flagged_high_risk"):
            logger.info("Case %d already in terminal state: %s", case_id, case.current_state)
            return {
                "case_id": case_id,
                "status": "already_terminal",
                "current_state": case.current_state,
                "stopping_reason": "terminal_case",
            }

        if case.current_state == "awaiting_pic_upload":
            return {
                "case_id": case_id,
                "status": "awaiting_pic_upload",
                "current_state": case.current_state,
                "stages_run": [],
                "results": {},
                "trace": [],
                "agentic_trace": [],
                "stopping_reason": "awaiting_pic_upload",
            }

        uploaded_file = UploadedFile.query.filter_by(
            vendor_case_id=case_id
        ).first()
        if case.current_state == "pending_follow_up" and EmailDraft.query.filter_by(
            vendor_case_id=case_id
        ).first():
            return {
                "case_id": case_id,
                "status": "awaiting_vendor_information",
                "current_state": case.current_state,
                "stages_run": [],
                "results": {},
                "trace": [],
                "agentic_trace": [],
                "stopping_reason": "follow_up_created",
            }

        if not uploaded_file:
            result = {
                "case_id": case_id,
                "current_state": case.current_state,
                "stages_run": [],
                "results": {},
                "trace": [],
            }
            plan = {
                "action": "request_pic_upload",
                "reason": "No documents are attached; request the PIC to upload the case documents.",
                "target_fields": [],
                "target_stage": None,
            }
            started_at = datetime.utcnow()
            started_clock = time.perf_counter()
            execution = ActionExecutor.execute(case_id, plan)
            case = VendorCase.query.get(case_id)
            result["agentic_trace"] = [
                WorkflowOrchestrator._trace_agentic_action(
                    1,
                    plan,
                    plan["reason"],
                    result["current_state"],
                    case.current_state,
                    execution["status"],
                    result["current_state"] != case.current_state,
                    started_at,
                    started_clock,
                    result=execution,
                )
            ]
            result["current_state"] = case.current_state
            result["stopping_reason"] = (
                "awaiting_pic_upload"
                if execution["status"] == "awaiting_pic_upload"
                else execution["status"]
            )
            return result

        results = {}
        trace = []

        if case.current_state in ("uploaded", "intake_received"):
            state_before = case.current_state
            stage_started_at = datetime.utcnow()
            stage_started_clock = time.perf_counter()
            try:
                extraction_result = WorkflowOrchestrator._summarize_extraction_result(
                    ExtractionOrchestrator.run(case_id)
                )
                if extraction_result["status"] == "failed":
                    case = VendorCase.query.get(case_id)
                    case.current_state = "manual_review_required"
                    db.session.commit()
            except Exception as error:
                logger.exception("Extraction failed for case %d", case_id)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                extraction_result = {
                    "status": "failed",
                    "error_type": type(error).__name__,
                }
            results["extraction"] = extraction_result
            case = VendorCase.query.get(case_id)
            trace.append(WorkflowOrchestrator._trace_stage(
                case_id, "extraction", state_before, case.current_state,
                extraction_result, started_at=stage_started_at,
                started_clock=stage_started_clock,
            ))

        if case.current_state in ("intake_classified", "procurement_form_detected", "procurement_form_missing",
                                   "intake_received", "uploaded", "extracting"):
            state_before = case.current_state
            stage_started_at = datetime.utcnow()
            stage_started_clock = time.perf_counter()
            form_result = FormService.structure_form(case_id)
            results["form_structuring"] = form_result
            case = VendorCase.query.get(case_id)
            trace.append(WorkflowOrchestrator._trace_stage(
                case_id, "form_structuring", state_before, case.current_state, form_result,
                started_at=stage_started_at, started_clock=stage_started_clock,
            ))

        if case.current_state in ("field_alignment_completed", "incomplete_after_autofill",
                                   "form_comparison_in_progress", "form_auto_fill_in_progress"):
            state_before = case.current_state
            stage_started_at = datetime.utcnow()
            stage_started_clock = time.perf_counter()
            ver_result = WorkflowOrchestrator._run_verification(case_id)
            results["verification"] = ver_result
            case = VendorCase.query.get(case_id)
            trace.append(WorkflowOrchestrator._trace_stage(
                case_id, "verification", state_before, case.current_state, ver_result,
                started_at=stage_started_at, started_clock=stage_started_clock,
            ))

        # Verification and policy evaluation use distinct states so the
        # workflow cannot accidentally skip policy evaluation after verification.
        # Re-running from policy_review_in_progress retries policy evaluation
        # instead of routing with potentially stale policy results.
        if case.current_state in ("verification_completed", "policy_review_in_progress"):
            state_before = case.current_state
            stage_started_at = datetime.utcnow()
            stage_started_clock = time.perf_counter()
            policy_result = WorkflowOrchestrator._run_policy_evaluation(case_id)
            results["policy_evaluation"] = policy_result
            case = VendorCase.query.get(case_id)
            trace.append(WorkflowOrchestrator._trace_stage(
                case_id, "policy_evaluation", state_before, case.current_state, policy_result,
                started_at=stage_started_at, started_clock=stage_started_clock,
            ))

        deterministic_action_trace = None
        deterministic_stopping_reason = None
        extraction_warnings = results.get("extraction", {}).get("ocr_warnings", [])
        # Routing only runs after policy evaluation has successfully completed.
        if case.current_state == "routing_in_progress":
            if extraction_warnings:
                plan = {
                    "action": "request_pic_upload",
                    "reason": (
                        "OCR was incomplete or uncertain; ask the PIC to correct "
                        "the affected values or upload a clearer document."
                    ),
                    "target_fields": [],
                    "target_stage": None,
                }
                (
                    deterministic_action_trace,
                    deterministic_stopping_reason,
                ) = WorkflowOrchestrator._execute_deterministic_action(case_id, plan)
            else:
                state_before = case.current_state
                stage_started_at = datetime.utcnow()
                stage_started_clock = time.perf_counter()
                routing_result = WorkflowOrchestrator._run_routing(case_id)
                results["routing"] = routing_result
                case = VendorCase.query.get(case_id)
                trace.append(WorkflowOrchestrator._trace_stage(
                    case_id, "routing", state_before, case.current_state, routing_result,
                    started_at=stage_started_at, started_clock=stage_started_clock,
                ))

        result = {
            "case_id": case_id,
            "current_state": case.current_state,
            "stages_run": list(results.keys()),
            "results": results,
            "trace": trace,
        }
        if include_agentic:
            recovery_attempts, failed_stage_actions = (
                WorkflowOrchestrator._retry_retryable_stage_failures(
                    case_id, results, trace
                )
            )
            if recovery_attempts:
                result["recovery_attempts"] = recovery_attempts
            case = VendorCase.query.get(case_id)
            if (
                case.current_state == "verification_blocked"
                and not failed_stage_actions
            ):
                WorkflowOrchestrator._route_to_manual_review(case_id)
                case = VendorCase.query.get(case_id)

            if failed_stage_actions and case.current_state == "manual_review_required":
                state_before = case.current_state
                case.current_state = "processing_failed"
                db.session.commit()
                trace.append(WorkflowOrchestrator._trace_stage(
                    case_id, "processing_failure_recovery", state_before,
                    case.current_state, {"status": "retry_exhausted"},
                ))

            if deterministic_action_trace is not None:
                result["agentic_trace"] = deterministic_action_trace
                result["stopping_reason"] = deterministic_stopping_reason
            elif case.current_state in {
                "completed", "rejected", "flagged_high_risk",
                "manual_review_required",
            }:
                result["agentic_trace"] = []
                result["stopping_reason"] = case.current_state
            elif failed_stage_actions:
                agentic_trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(
                    case_id, initial_actions=failed_stage_actions
                )
                result["agentic_trace"] = agentic_trace
                result["stopping_reason"] = stopping_reason
            else:
                snapshot = NextActionPlanner.case_snapshot(case_id, [])
                if WorkflowOrchestrator._needs_pic_correction(snapshot, results):
                    plan = {
                        "action": "request_pic_upload",
                        "reason": (
                            "OCR was incomplete or uncertain; ask the PIC to correct "
                            "the affected values or upload a clearer document."
                        ),
                        "target_fields": [],
                        "target_stage": None,
                    }
                    agentic_trace, stopping_reason = (
                        WorkflowOrchestrator._execute_deterministic_action(
                            case_id, plan
                        )
                    )
                    result["agentic_trace"] = agentic_trace
                    result["stopping_reason"] = stopping_reason
                else:
                    plan = WorkflowOrchestrator._deterministic_recovery_plan(snapshot)
                    if plan:
                        agentic_trace, stopping_reason = (
                            WorkflowOrchestrator._execute_deterministic_action(
                                case_id, plan
                            )
                        )
                        result["agentic_trace"] = agentic_trace
                        result["stopping_reason"] = stopping_reason
                    elif WorkflowOrchestrator._requires_agentic_planning(snapshot):
                        agentic_trace, stopping_reason = (
                            WorkflowOrchestrator._run_agentic_loop(case_id)
                        )
                        result["agentic_trace"] = agentic_trace
                        result["stopping_reason"] = stopping_reason
                    else:
                        result["agentic_trace"] = []
                        result["stopping_reason"] = "no_recovery_required"
            case = VendorCase.query.get(case_id)
            result["current_state"] = case.current_state
        return result

    @staticmethod
    def _retry_retryable_stage_failures(case_id, results, trace):
        from app.orchestrators.reprocessing_orchestrator import (
            ReprocessingOrchestrator,
        )

        attempts = []
        failed_actions = []
        pending = list(results.items())
        processed_stages = set()
        while pending:
            stage, stage_result = pending.pop(0)
            if (
                stage in processed_stages
                or not isinstance(stage_result, dict)
                or stage_result.get("status") not in {"failed", "glm_failed"}
                or stage_result.get("retryable") is not True
            ):
                continue
            processed_stages.add(stage)

            case = VendorCase.query.get(case_id)
            state_before = case.current_state
            started_at = datetime.utcnow()
            started_clock = time.perf_counter()
            try:
                retry_result = ReprocessingOrchestrator.reprocess(
                    case_id, stage, "automatic_retry"
                )
            except Exception as error:
                logger.exception(
                    "Automatic retry failed before execution for case %d at %s",
                    case_id,
                    stage,
                )
                retry_result = {
                    "status": "not_started",
                    "stage": stage,
                    "error": str(error),
                }
                WorkflowOrchestrator._route_to_manual_review(case_id)

            retry_status = retry_result.get("status", "failed")
            if retry_result.get("stage_result_summary") is not None:
                results[stage] = retry_result["stage_result_summary"]
            attempts.append({
                "stage": stage,
                "status": retry_status,
                "reprocessing_run_id": retry_result.get("reprocessing_run_id"),
            })
            case = VendorCase.query.get(case_id)
            trace.append(WorkflowOrchestrator._trace_stage(
                case_id,
                "automatic_retry",
                state_before,
                case.current_state,
                retry_result,
                started_at=started_at,
                started_clock=started_clock,
            ))

            if retry_status in {"failed", "retry_limit_reached"}:
                failed_actions.append({
                    "action": "reprocess_stage",
                    "target_stage": stage,
                    "target_fields": [],
                    "outcome": "failed",
                    "progress": False,
                    "reason": (
                        f"The single deterministic retry for {stage} did not "
                        "resolve the technical failure."
                    ),
                    "result": {
                        "status": retry_status,
                        "stage": stage,
                        "reprocessing_run_id": retry_result.get(
                            "reprocessing_run_id"
                        ),
                    },
                })
            workflow_results = retry_result.get("workflow_result", {}).get(
                "results", {}
            )
            pending.extend(
                (downstream_stage, downstream_result)
                for downstream_stage, downstream_result in workflow_results.items()
                if downstream_stage not in processed_stages
                and isinstance(downstream_result, dict)
                and downstream_result.get("status") in {"failed", "glm_failed"}
                and downstream_result.get("retryable") is True
            )
        return attempts, failed_actions

    @staticmethod
    def _needs_pic_correction(snapshot, results):
        extraction = results.get("extraction") or {}
        if extraction.get("ocr_warnings"):
            return True
        return any(
            field.get("follow_up_eligible")
            and field.get("status") == "low_confidence"
            for field in snapshot.get("fields", [])
        )

    @staticmethod
    def _deterministic_recovery_plan(snapshot):
        candidates = snapshot.get("follow_up_candidates", [])
        evidence_assessment = snapshot.get("case_evidence_assessment", {})
        if not candidates or evidence_assessment.get("ambiguous_fields"):
            return None
        if evidence_assessment.get("documents_added_after_last_structuring"):
            return None
        if any(
            candidate.get("availability") != "not_found_in_reviewed_documents"
            for candidate in candidates
        ):
            return None
        return {
            "action": "request_information",
            "reason": (
                "Required information was not found after reviewing the submitted documents."
            ),
            "target_fields": [],
            "target_stage": None,
        }

    @staticmethod
    def _requires_agentic_planning(snapshot):
        assessment = snapshot.get("case_evidence_assessment", {})
        if (
            assessment.get("ambiguous_fields")
            or assessment.get("documents_added_after_last_structuring")
        ):
            return True
        if any(
            field.get("follow_up_eligible") and field.get("evidence_available")
            for field in snapshot.get("fields", [])
        ):
            return True
        verification = snapshot.get("verification") or {}
        if (
            verification.get("mismatch_severity") in {"moderate", "critical"}
            or verification.get("critical_conflicts")
        ):
            return True
        policy = snapshot.get("policy") or {}
        return (
            policy.get("risk_band") in {"high", "critical"}
            or policy.get("blocking_issue_count", 0) > 0
        )

    @staticmethod
    def _execute_deterministic_action(case_id, plan):
        case = VendorCase.query.get(case_id)
        state_before = case.current_state
        started_at = datetime.utcnow()
        started_clock = time.perf_counter()
        try:
            execution = ActionExecutor.execute(case_id, plan)
        except Exception as error:
            logger.exception(
                "Deterministic recovery action failed for case %d", case_id
            )
            WorkflowOrchestrator._route_to_manual_review(case_id)
            case = VendorCase.query.get(case_id)
            return [
                WorkflowOrchestrator._trace_agentic_action(
                    1,
                    plan,
                    plan["reason"],
                    state_before,
                    case.current_state,
                    "execution_failed",
                    False,
                    started_at,
                    started_clock,
                    failure_reason=str(error),
                )
            ], "manual_review_required"

        case = VendorCase.query.get(case_id)
        status = execution.get("status", "completed")
        if status in {"failed", "error", "glm_failed", "blocked_terminal_case"}:
            WorkflowOrchestrator._route_to_manual_review(case_id)
            case = VendorCase.query.get(case_id)
        trace = WorkflowOrchestrator._trace_agentic_action(
            1,
            plan,
            plan["reason"],
            state_before,
            case.current_state,
            status,
            state_before != case.current_state,
            started_at,
            started_clock,
            result=WorkflowOrchestrator._safe_agentic_result(execution),
            failure_reason=(
                f"Action returned {status}."
                if status in {"failed", "error", "glm_failed", "blocked_terminal_case"}
                else None
            ),
        )
        if plan["action"] == "request_pic_upload" and status == "awaiting_pic_upload":
            stopping_reason = "awaiting_pic_upload"
        elif plan["action"] == "request_information" and status in {
            "completed", "already_available",
        }:
            stopping_reason = "follow_up_created"
        elif status == "glm_failed":
            stopping_reason = "follow_up_generation_failed"
        elif status in {"failed", "error", "blocked_terminal_case"}:
            stopping_reason = "execution_failed"
        else:
            stopping_reason = "no_meaningful_progress"
        return [trace], stopping_reason

    @staticmethod
    def _run_agentic_loop(case_id, initial_actions=None):
        case = VendorCase.query.get(case_id)
        if case.current_state in {"completed", "rejected", "flagged_high_risk"}:
            return [], "terminal_case"
        if case.current_state == "manual_review_required":
            return [], "manual_review_required"

        max_actions = max(1, int(current_app.config.get("MAX_AGENTIC_ACTIONS", 3)))
        attempted_actions = list(initial_actions or [])
        seen_actions = {}
        action_trace = []
        terminal_states = {"completed", "rejected", "flagged_high_risk"}
        stopping_reason = "maximum_actions_reached"

        for sequence in range(1, max_actions + 1):
            case = VendorCase.query.get(case_id)
            if case.current_state in terminal_states:
                stopping_reason = "terminal_case"
                break
            if case.current_state == "manual_review_required":
                stopping_reason = "manual_review_required"
                break

            started_at = datetime.utcnow()
            started_clock = time.perf_counter()
            try:
                before = NextActionPlanner.case_snapshot(case_id, attempted_actions)
                plan = WorkflowOrchestrator._registry_recovery_plan(
                    attempted_actions, before
                )
                if plan is None:
                    plan = NextActionPlanner.plan(
                        case_id, attempted_actions, snapshot=before
                    )
            except (GlmServiceError, NextActionPlannerError) as error:
                logger.error("Next-action planner failed for case %d: %s", case_id, error)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, None, str(error), case.current_state,
                    "manual_review_required", "planner_failed", False,
                    started_at, started_clock, failure_reason=str(error),
                ))
                stopping_reason = "planner_failure"
                break
            except Exception as error:
                logger.exception("Unexpected next-action planner failure for case %d", case_id)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, None, str(error), case.current_state,
                    "manual_review_required", "planner_failed", False,
                    started_at, started_clock, failure_reason=str(error),
                ))
                stopping_reason = "planner_failure"
                break

            action_key = (
                plan["action"],
                tuple(sorted(plan["target_fields"])),
                plan["target_stage"],
            )
            before_fingerprint = WorkflowOrchestrator._snapshot_fingerprint(before)
            try:
                current_snapshot = NextActionPlanner.case_snapshot(
                    case_id, attempted_actions
                )
            except Exception as error:
                logger.exception("Failed to verify planned workflow state for case %d", case_id)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], before["current_state"],
                    VendorCase.query.get(case_id).current_state,
                    "state_reload_failed", False, started_at, started_clock,
                    failure_reason=str(error),
                ))
                stopping_reason = "state_reload_failure"
                break
            if (
                WorkflowOrchestrator._snapshot_fingerprint(current_snapshot)
                != before_fingerprint
            ):
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], before["current_state"],
                    current_snapshot["current_state"], "stale_plan_blocked",
                    False, started_at, started_clock,
                    failure_reason="Case evidence changed after the plan was created",
                ))
                stopping_reason = "stale_plan"
                break
            if seen_actions.get(action_key) == before_fingerprint:
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], case.current_state,
                    case.current_state, "duplicate_action_blocked", False,
                    started_at, started_clock,
                    failure_reason="Action with identical targets was already attempted",
                ))
                stopping_reason = "duplicate_action"
                break
            seen_actions[action_key] = before_fingerprint

            if plan["action"] == "finish":
                if before["current_state"] == "processing_failed":
                    WorkflowOrchestrator._route_to_manual_review(case_id)
                    state_after = VendorCase.query.get(case_id).current_state
                    stopping_reason = "manual_review_required"
                else:
                    state_after = case.current_state
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], case.current_state,
                    state_after, "finished", False, started_at, started_clock,
                ))
                attempted_actions.append({
                    "action": plan["action"],
                    "reason": plan["reason"],
                    "outcome": "finished",
                })
                if stopping_reason != "manual_review_required":
                    stopping_reason = "planner_finish"
                break

            try:
                execution = ActionExecutor.execute(case_id, plan)
            except (ActionExecutorError, GlmServiceError) as error:
                logger.error("Next-action execution failed for case %d: %s", case_id, error)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                after_state = VendorCase.query.get(case_id).current_state
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], case.current_state,
                    after_state, "execution_failed", False,
                    started_at, started_clock, failure_reason=str(error),
                ))
                stopping_reason = "execution_failure"
                break
            except Exception as error:
                logger.exception("Unexpected next-action execution failure for case %d", case_id)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                after_state = VendorCase.query.get(case_id).current_state
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], case.current_state,
                    after_state, "execution_failed", False,
                    started_at, started_clock, failure_reason=str(error),
                ))
                stopping_reason = "execution_failure"
                break

            try:
                after = NextActionPlanner.case_snapshot(case_id, attempted_actions)
            except Exception as error:
                logger.exception("Failed to reload workflow state for case %d", case_id)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                after_state = VendorCase.query.get(case_id).current_state
                action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                    sequence, plan, plan["reason"], before["current_state"],
                    after_state, "state_reload_failed", False,
                    started_at, started_clock, result=execution,
                    failure_reason=str(error),
                ))
                stopping_reason = "state_reload_failure"
                break
            progress = WorkflowOrchestrator._snapshot_fingerprint(
                before
            ) != WorkflowOrchestrator._snapshot_fingerprint(after)
            seen_actions[action_key] = WorkflowOrchestrator._snapshot_fingerprint(after)
            if (
                plan["action"] == "targeted_verification"
                and execution.get("match_status") in {"matched", "mismatched"}
            ):
                progress = True

            safe_execution = WorkflowOrchestrator._safe_agentic_result(execution)
            action_trace.append(WorkflowOrchestrator._trace_agentic_action(
                sequence, plan, plan["reason"], before["current_state"],
                after["current_state"], execution.get("status", "completed"),
                progress, started_at, started_clock,
                result=safe_execution,
                failure_reason=(
                    f"Action returned {execution.get('status')}."
                    if execution.get("status") in {
                        "failed", "error", "glm_failed", "blocked_terminal_case",
                    }
                    else None
                ),
            ))
            attempted_actions.append({
                "action": plan["action"],
                "reason": plan["reason"],
                "target_fields": plan["target_fields"],
                "target_stage": plan["target_stage"],
                "outcome": execution.get("status", "completed"),
                "progress": progress,
                "result": safe_execution,
            })

            if plan["action"] == "request_information" and execution.get("status") in {
                "completed", "already_available"
            }:
                stopping_reason = "follow_up_created"
                break
            if (
                plan["action"] == "request_pic_upload"
                and execution.get("status") == "awaiting_pic_upload"
            ):
                stopping_reason = "awaiting_pic_upload"
                break

            if plan["action"] == "manual_review":
                stopping_reason = "manual_review_required"
                break
            if execution.get("status") in {
                "failed", "error", "glm_failed", "blocked_terminal_case",
            }:
                WorkflowOrchestrator._route_to_manual_review(case_id)
                stopping_reason = "execution_failed_or_blocked"
                break
            if not progress:
                if (
                    after["current_state"] != "pending_follow_up"
                    and WorkflowOrchestrator._has_unresolved_issues(after)
                ):
                    WorkflowOrchestrator._route_to_manual_review(case_id)
                    action_trace[-1]["state_after"] = "manual_review_required"
                    stopping_reason = "no_progress_manual_review"
                    break
                stopping_reason = "no_meaningful_progress"
                break
        else:
            stopping_reason = "maximum_actions_reached"

        if stopping_reason == "maximum_actions_reached":
            try:
                snapshot = NextActionPlanner.case_snapshot(case_id, attempted_actions)
            except Exception as error:
                logger.exception("Failed to inspect unresolved workflow state for case %d", case_id)
                WorkflowOrchestrator._route_to_manual_review(case_id)
                return action_trace, "state_reload_failure"
            if (
                WorkflowOrchestrator._has_unresolved_issues(snapshot)
                and snapshot["current_state"] != "pending_follow_up"
            ):
                WorkflowOrchestrator._route_to_manual_review(case_id)
                if action_trace:
                    action_trace[-1]["state_after"] = "manual_review_required"
                stopping_reason = "maximum_actions_reached_manual_review"

        return action_trace, stopping_reason

    @staticmethod
    def _registry_recovery_plan(prior_actions, snapshot):
        if not prior_actions:
            return None

        last_action = prior_actions[-1]
        result = last_action.get("result")
        if (
            last_action.get("action") != "targeted_verification"
            or not isinstance(result, dict)
            or result.get("match_status") not in {"mismatched", "no_match"}
        ):
            return None

        unresolved_registration_fields = set(
            result.get("unresolved_fields", [])
        )
        unresolved_follow_up_candidates = {
            item.get("field_name"): item.get("availability")
            for item in snapshot.get("follow_up_candidates", [])
            if isinstance(item, dict)
        }
        if (
            unresolved_registration_fields
            & {
                field_name
                for field_name, availability in unresolved_follow_up_candidates.items()
                if availability == "not_found_in_reviewed_documents"
            }
            and snapshot.get("follow_up_draft_count", 0) == 0
        ):
            return {
                "action": "request_information",
                "reason": (
                    "The internal registry did not corroborate a submitted "
                    "registration identifier; request clarification."
                ),
                "target_fields": [],
                "target_stage": None,
            }

        return {
            "action": "manual_review",
            "reason": (
                "The internal registry did not corroborate a submitted "
                "registration identifier and automated follow-up is unavailable."
            ),
            "target_fields": [],
            "target_stage": None,
        }

    @staticmethod
    def _snapshot_fingerprint(snapshot):
        return json.dumps({
            "current_state": snapshot["current_state"],
            "fields": snapshot["fields"],
            "available_evidence": snapshot.get("available_evidence", []),
            "case_evidence_assessment": snapshot.get("case_evidence_assessment", {}),
            "unchanged_successful_stages": snapshot.get(
                "unchanged_successful_stages", []
            ),
            "verification": snapshot["verification"],
            "policy": snapshot["policy"],
            "routing_decision": snapshot["routing_decision"],
            "follow_up_draft_count": snapshot["follow_up_draft_count"],
        }, sort_keys=True)

    @staticmethod
    def _safe_agentic_result(result):
        safe_keys = {
            "status",
            "source",
            "match_status",
            "matched_vendor_number",
            "target_fields",
            "unresolved_fields",
            "external_verification_performed",
            "stage",
            "result_changed",
            "downstream_stages",
            "current_state",
            "email_draft_id",
            "reprocessing_run_id",
        }
        return {
            key: value
            for key, value in result.items()
            if key in safe_keys
        }

    @staticmethod
    def _summarize_extraction_result(result):
        extraction_results = result.get("extraction_results", [])
        technical_failures = [
            {
                "file_id": item.get("file_id"),
                "status": item.get("status", "unknown"),
                "extraction_method": item.get("extraction_method", "unknown"),
            }
            for item in extraction_results
            if item.get("status") != "completed"
        ]
        ocr_warnings = [
            {
                "file_id": item.get("file_id"),
                "status": item.get("status", "unknown"),
                "quality_status": item.get("ocr_quality", {}).get(
                    "quality_status", "unknown"
                ),
                "average_confidence": item.get("ocr_quality", {}).get(
                    "average_confidence"
                ),
                "minimum_confidence": item.get("ocr_quality", {}).get(
                    "minimum_confidence"
                ),
                "low_confidence_block_count": item.get("ocr_quality", {}).get(
                    "low_confidence_block_count", 0
                ),
            }
            for item in extraction_results
            if item.get("extraction_method") == "paddleocr"
            and (
                item.get("status") != "completed"
                or item.get("ocr_quality", {}).get("quality_status")
                in {"low_confidence", "unreadable"}
            )
        ]
        failures = technical_failures
        retryable = bool(technical_failures) and all(
            failure["extraction_method"] == "paddleocr"
            and failure["status"].startswith("ocr_error:")
            for failure in technical_failures
        )
        return {
            "status": "failed" if technical_failures else "completed",
            "current_state": result.get("current_state"),
            "files_processed": result.get("files_processed", 0),
            "zip_files_extracted": result.get("zip_files_extracted", 0),
            "failures": failures,
            "technical_failures": technical_failures,
            "retryable": retryable,
            "ocr_warnings": ocr_warnings,
        }

    @staticmethod
    def _is_retryable_glm_error(error):
        return GlmService.is_retryable_error(error)

    @staticmethod
    def _has_unresolved_issues(snapshot):
        unresolved_statuses = {
            "missing", "conflicting", "unverifiable",
            "low_confidence", "declaration_missing",
            "mismatched_with_evidence",
        }
        if any(
            field.get(
                "follow_up_eligible",
                field.get("field_name") in FOLLOW_UP_FIELDS,
            )
            and field["status"] in unresolved_statuses
            for field in snapshot["fields"]
        ):
            return True
        policy = snapshot["policy"]
        if policy and (
            policy["blocking_issue_count"] > 0
            or policy["recoverable_issue_count"] > 0
            or policy["risk_band"] in {"high", "critical"}
        ):
            return True
        verification = snapshot["verification"]
        return bool(
            verification
            and (
                verification["mismatch_severity"] in {"moderate", "critical"}
                or verification["legitimacy_status"] in {
                    "conflicting", "unverifiable", "suspicious",
                }
            )
        )

    @staticmethod
    def _route_to_manual_review(case_id):
        case = VendorCase.query.get(case_id)
        if case and case.current_state not in {
            "completed", "rejected", "flagged_high_risk",
        }:
            case.current_state = "manual_review_required"
            db.session.commit()

    @staticmethod
    def _trace_agentic_action(
        sequence, plan, reason, state_before, state_after, status, progress,
        started_at, started_clock, result=None, failure_reason=None,
    ):
        completed_at = datetime.utcnow()
        return {
            "sequence": sequence,
            "action": plan["action"] if plan else None,
            "reason": reason,
            "target_fields": plan["target_fields"] if plan else [],
            "target_stage": plan["target_stage"] if plan else None,
            "status": status,
            "state_before": state_before,
            "state_after": state_after,
            "started_at": started_at.isoformat() + "Z",
            "completed_at": completed_at.isoformat() + "Z",
            "duration_ms": round((time.perf_counter() - started_clock) * 1000, 2),
            "progress": progress,
            "result": result,
            "failure_reason": failure_reason,
        }

    @staticmethod
    def _trace_stage(case_id, stage, state_before, state_after, result, started_at=None, started_clock=None):
        """Return a compact, timed execution trace without copying sensitive payloads."""
        completed_at = datetime.utcnow()
        status = result.get("status", "completed") if isinstance(result, dict) else "completed"
        duration_ms = (
            round((time.perf_counter() - started_clock) * 1000, 2)
            if started_clock is not None
            else None
        )
        trace_entry = {
            "stage": stage,
            "state_before": state_before,
            "state_after": state_after,
            "status": status,
            "started_at": started_at.isoformat() + "Z" if started_at else None,
            "completed_at": completed_at.isoformat() + "Z",
            "duration_ms": duration_ms,
        }
        logger.info(
            "Workflow stage completed: case_id=%s stage=%s status=%s "
            "state_before=%s state_after=%s duration_ms=%s",
            case_id,
            stage,
            status,
            state_before,
            state_after,
            duration_ms,
        )
        return trace_entry

    @staticmethod
    def _run_verification(case_id):
        case = VendorCase.query.get(case_id)
        case.current_state = "verification_in_progress"
        db.session.commit()

        context = ContextAssembler.verification(case_id)
        prompt = VERIFICATION_PROMPT.format(**context)

        try:
            glm_result = GlmService.call_glm(
                prompt=prompt,
                response_schema=VERIFICATION_SCHEMA,
                max_tokens=4096,
            )
        except GlmServiceError as e:
            logger.error("Verification GLM failed for case %d: %s", case_id, e)
            case.current_state = "manual_review_required"
            db.session.commit()
            return {
                "status": "glm_failed",
                "error": str(e),
                "retryable": WorkflowOrchestrator._is_retryable_glm_error(e),
            }

        mismatch_severity = WorkflowOrchestrator._normalized_verification_severity(
            glm_result
        )

        run = VerificationRun(
            vendor_case_id=case_id,
            run_status="completed",
            legitimacy_status=glm_result.get("legitimacy_status"),
            mismatch_severity=mismatch_severity,
            summary=glm_result.get("summary"),
            key_findings=json.dumps(glm_result.get("critical_conflicts", [])),
            confidence_score=glm_result.get("confidence"),
            completed_at=datetime.utcnow(),
        )
        db.session.add(run)

        case.latest_verification_status = glm_result.get("legitimacy_status")
        case.current_state = "verification_completed"
        db.session.commit()

        return {
            "legitimacy_status": glm_result.get("legitimacy_status"),
            "mismatch_severity": mismatch_severity,
            "confidence": glm_result.get("confidence"),
            "summary": glm_result.get("summary"),
        }

    @staticmethod
    def _normalized_verification_severity(glm_result):
        mismatch_severity = glm_result.get("mismatch_severity")
        mismatch_lists = (
            "critical_conflicts",
            "minor_mismatches",
            "moderate_mismatches",
            "critical_mismatches",
        )
        if (
            mismatch_severity is None
            and glm_result.get("legitimacy_status")
            in {"verified", "verified_with_minor_gaps"}
            and all(
                isinstance(glm_result.get(key), list)
                and not glm_result[key]
                for key in mismatch_lists
            )
        ):
            return "minor"
        return mismatch_severity

    @staticmethod
    def _run_policy_evaluation(case_id):
        case = VendorCase.query.get(case_id)
        case.current_state = "policy_review_in_progress"
        db.session.commit()

        context = ContextAssembler.policy_evaluation(case_id)
        prompt = POLICY_EVALUATION_PROMPT.format(**context)

        try:
            glm_result = GlmService.call_glm(
                prompt=prompt,
                response_schema=POLICY_EVALUATION_SCHEMA,
                max_tokens=4096,
            )
        except GlmServiceError as e:
            logger.error("Policy evaluation GLM failed for case %d: %s", case_id, e)
            case.current_state = "manual_review_required"
            db.session.commit()
            return {
                "status": "glm_failed",
                "error": str(e),
                "retryable": WorkflowOrchestrator._is_retryable_glm_error(e),
            }

        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        glm_result = WorkflowOrchestrator._normalize_policy_result(
            glm_result, fields, case.registration_type
        )

        latest_verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()

        run = PolicyEvaluationRun(
            vendor_case_id=case_id,
            verification_run_id=latest_verification.id if latest_verification else None,
            risk_band=glm_result.get("risk_band"),
            blocking_issue_count=glm_result.get("blocking_issue_count", 0),
            recoverable_issue_count=glm_result.get("recoverable_issue_count", 0),
            policy_summary=glm_result.get("policy_summary"),
            risk_flags=json.dumps(glm_result.get("risk_flags", [])),
            policy_confidence_score=glm_result.get("policy_confidence"),
            completed_at=datetime.utcnow(),
        )
        db.session.add(run)

        case.latest_policy_status = glm_result.get("risk_band")
        case.risk_band = glm_result.get("risk_band")
        case.current_state = "routing_in_progress"
        db.session.commit()

        return {
            "risk_band": glm_result.get("risk_band"),
            "blocking_issue_count": glm_result.get("blocking_issue_count"),
            "recoverable_issue_count": glm_result.get("recoverable_issue_count"),
            "policy_confidence": glm_result.get("policy_confidence"),
        }

    @staticmethod
    def _normalize_policy_result(result, fields, registration_type):
        field_values = {
            field.field_name: (
                getattr(field, "normalized_value_text", None)
                or getattr(field, "value_text", None)
                or ""
            ).strip().casefold()
            for field in fields
        }
        optional_fields = set(CANONICAL_FIELD_NAMES) - FOLLOW_UP_FIELDS
        if field_values.get("iso_certification_available", "") not in SafetyGate.AFFIRMATIVE_VALUES:
            optional_fields.update({
                "iso_certification_number",
                "iso_certification_expiry_date",
            })

        def normalized_name(value):
            return str(value).casefold().replace("_", " ")

        def is_optional_issue(item):
            if isinstance(item, dict):
                field_name = str(item.get("field_name") or "").casefold()
                category = str(item.get("flag_type") or "").casefold()
                description = str(item.get("description") or "").casefold()
                if field_name and field_name in optional_fields:
                    return True
                if category in {
                    "declaration_incomplete",
                    "outsourcing_risk_flag",
                }:
                    return True
                text = " ".join((field_name, category, description))
            else:
                text = str(item).casefold()
            text = text.replace("_", " ")
            if any(
                marker in text
                for marker in (
                    "declaration",
                    "outsourcing",
                    "outsource",
                    "legal dispute",
                    "signatory",
                    "company chop",
                    "authorized signature",
                )
            ):
                return True
            if any(
                normalized_name(field_name) in text
                for field_name in pic_supplied_fields
            ):
                return True
            return any(
                normalized_name(field_name) in text
                for field_name in optional_fields
            )

        pic_supplied_fields = {
            field.field_name
            for field in fields
            if SafetyGate.has_pic_supplied_value(field)
        }
        normalized = dict(result)
        removed_optional_issue = False
        for key in ("risk_flags", "blocking_issues", "recoverable_issues"):
            items = result.get(key, [])
            if not isinstance(items, list):
                continue
            retained = []
            for item in items:
                if is_optional_issue(item):
                    removed_optional_issue = True
                else:
                    retained.append(item)
            normalized[key] = retained

        risk_flags = normalized.get("risk_flags", [])
        blocking_flag_count = sum(
            1 for flag in risk_flags
            if isinstance(flag, dict) and flag.get("severity") == "blocking"
        )
        recoverable_flag_count = sum(
            1 for flag in risk_flags
            if isinstance(flag, dict) and flag.get("severity") == "recoverable"
        )
        normalized["blocking_issue_count"] = max(
            len(normalized.get("blocking_issues", [])), blocking_flag_count
        )
        normalized["recoverable_issue_count"] = max(
            len(normalized.get("recoverable_issues", [])), recoverable_flag_count
        )

        if (
            removed_optional_issue
            and normalized["blocking_issue_count"] == 0
            and normalized["recoverable_issue_count"] == 0
            and not normalized.get("risk_flags")
            and not normalized.get("blocking_issues")
            and not normalized.get("recoverable_issues")
        ):
            normalized["risk_band"] = "low"
            normalized["policy_summary"] = (
                "No in-scope policy issues remain after excluding findings "
                "outside the required vendor field scope."
            )
            normalized["recommended_next_step"] = (
                "Proceed to routing based on the in-scope findings."
            )
        return normalized

    @staticmethod
    def _apply_safety_gate(
        case_id, decision_code, decision_reasoning, recommended_action, confidence=None
    ):
        """Veto approvals with weak LLM confidence or failed deterministic checks."""
        if decision_code != "complete_verified":
            return decision_code, decision_reasoning, recommended_action, None

        # The routing prompt requires at least 0.8 confidence for a clearly
        # evidence-supported decision. Enforce that rule in Python as well.
        confidence_is_valid = (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and math.isfinite(confidence)
            and 0.8 <= confidence <= 1.0
        )
        if not confidence_is_valid:
            gate_result = {
                "allowed": False,
                "suggested_decision": "manual_review_required",
                "reasons": [
                    "approval_confidence_below_threshold"
                    if isinstance(confidence, (int, float))
                    and not isinstance(confidence, bool)
                    and math.isfinite(confidence)
                    else "approval_confidence_missing_or_invalid"
                ],
                "recoverable_fields": [],
                "review_blockers": [],
            }
            decision_reasoning = (
                decision_reasoning
                + " | Safety gate blocked automatic approval: "
                + gate_result["reasons"][0]
            ).strip(" |")
            return (
                "manual_review_required",
                decision_reasoning,
                "Escalate for human review",
                gate_result,
            )

        gate_result = SafetyGate.evaluate(case_id)
        if gate_result["allowed"]:
            return decision_code, decision_reasoning, recommended_action, gate_result

        decision_code = gate_result["suggested_decision"]
        reasons = ", ".join(gate_result["reasons"])
        decision_reasoning = (
            decision_reasoning
            + " | Safety gate blocked automatic approval: "
            + reasons
        ).strip(" |")
        recommended_action = (
            "Request missing information"
            if decision_code == "follow_up_required"
            else "Escalate for human review"
            if decision_code == "manual_review_required"
            else "Escalate to high-risk procurement review"
        )
        logger.warning(
            "Safety gate blocked approval for case %d: %s",
            case_id,
            reasons,
        )
        return decision_code, decision_reasoning, recommended_action, gate_result

    @staticmethod
    def _normalize_decision_evidence(value):
        """Keep only well-formed source/finding pairs from routing model output."""
        if not isinstance(value, list):
            return []

        evidence = []
        for item in value:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            finding = item.get("finding")
            if not isinstance(source, str) or not isinstance(finding, str):
                continue
            source = source.strip()
            finding = finding.strip()
            if source and finding:
                evidence.append({"source": source, "finding": finding})
        return evidence
    @staticmethod
    def _run_routing(case_id):
        case = VendorCase.query.get(case_id)

        context = ContextAssembler.routing(case_id)
        prompt = ROUTING_PROMPT.format(**context)

        try:
            glm_result = GlmService.call_glm(
                prompt=prompt,
                response_schema=ROUTING_SCHEMA,
                max_tokens=2048,
            )
        except GlmServiceError as e:
            logger.error("Routing GLM failed for case %d: %s", case_id, e)
            case.current_state = "manual_review_required"
            db.session.commit()
            return {
                "status": "glm_failed",
                "error": str(e),
                "retryable": WorkflowOrchestrator._is_retryable_glm_error(e),
            }

        decision_code = glm_result.get("decision_code")
        decision_reasoning = glm_result.get("decision_reasoning_summary", "")
        recommended_action = glm_result.get("recommended_procurement_action")
        decision_evidence = WorkflowOrchestrator._normalize_decision_evidence(
            glm_result.get("decision_evidence", [])
        )
        latest_verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()
        latest_policy = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()

        # The LLM may recommend approval, but deterministic Python rules
        # can veto it before the decision is persisted.
        (
            decision_code,
            decision_reasoning,
            recommended_action,
            safety_gate_result,
        ) = WorkflowOrchestrator._apply_safety_gate(
            case_id,
            decision_code,
            decision_reasoning,
            recommended_action,
            glm_result.get("confidence"),
        )
        (
            decision_code,
            decision_reasoning,
            recommended_action,
        ) = WorkflowOrchestrator._prevent_rejection_for_noncritical_mismatch(
            decision_code,
            decision_reasoning,
            recommended_action,
            latest_verification,
        )
        (
            decision_code,
            decision_reasoning,
            recommended_action,
        ) = WorkflowOrchestrator._enforce_follow_up_eligibility(
            case_id,
            decision_code,
            decision_reasoning,
            recommended_action,
            glm_result.get("confidence"),
        )

        decision = RoutingDecision(
            vendor_case_id=case_id,
            verification_run_id=latest_verification.id if latest_verification else None,
            policy_evaluation_run_id=latest_policy.id if latest_policy else None,
            decision_code=decision_code,
            recommended_procurement_action=recommended_action,
            decision_reasoning_summary=decision_reasoning,
            decision_reason_codes=json.dumps(
                safety_gate_result.get("reasons", [])
                if safety_gate_result and safety_gate_result.get("reasons")
                else ["safety_gate_passed"]
                if safety_gate_result and safety_gate_result.get("allowed")
                else ["routing_decision:" + str(decision_code)]
            ),
            safety_gate_result=(
                json.dumps(safety_gate_result) if safety_gate_result is not None else None
            ),
            decision_evidence=json.dumps(decision_evidence),
            confidence_score=glm_result.get("confidence"),
        )
        db.session.add(decision)

        case.latest_routing_decision = decision_code

        state_map = {
            "complete_verified": "completed",
            "follow_up_required": "pending_follow_up",
            "flagged_high_risk": "flagged_high_risk",
            "rejected": "rejected",
            "manual_review_required": "manual_review_required",
        }
        case.current_state = state_map.get(decision_code, "manual_review_required")
        db.session.commit()

        return {
            "decision_code": decision_code,
            "reasoning": decision_reasoning,
            "recommended_procurement_action": recommended_action,
            "confidence": glm_result.get("confidence"),
            "next_state": case.current_state,
            "safety_gate": safety_gate_result,
            "decision_evidence": decision_evidence,
        }

    @staticmethod
    def _enforce_follow_up_eligibility(
        case_id, decision_code, decision_reasoning, recommended_action, confidence
    ):
        if decision_code != "follow_up_required":
            return decision_code, decision_reasoning, recommended_action

        fields = EyeVenFormFieldValue.query.filter_by(
            vendor_case_id=case_id
        ).all()
        field_values = {
            field.field_name: SafetyGate._field_value(field)
            for field in fields
        }
        if any(
            SafetyGate.is_follow_up_needed(field, field_values)
            for field in fields
        ):
            return decision_code, decision_reasoning, recommended_action

        gated_decision, gated_reasoning, gated_action, _ = (
            WorkflowOrchestrator._apply_safety_gate(
                case_id,
                "complete_verified",
                (
                    decision_reasoning
                    + " | Follow-up removed because no unresolved eligible "
                    "fields remain."
                ).strip(" |"),
                "Proceed with vendor onboarding",
                confidence,
            )
        )
        return gated_decision, gated_reasoning, gated_action

    @staticmethod
    def _prevent_rejection_for_noncritical_mismatch(
        decision_code, decision_reasoning, recommended_action, verification
    ):
        if (
            decision_code == "rejected"
            and verification is not None
            and verification.mismatch_severity != "critical"
        ):
            severity = verification.mismatch_severity or "unknown"
            decision_reasoning = (
                decision_reasoning
                + " | Rejection changed to manual review because verification "
                + f"severity is {severity}, not critical."
            ).strip(" |")
            return (
                "manual_review_required",
                decision_reasoning,
                "Escalate for human review",
            )
        return decision_code, decision_reasoning, recommended_action

    @staticmethod
    def _run_follow_up(case_id):
        context = ContextAssembler.follow_up(case_id)
        prompt = FOLLOW_UP_PROMPT.format(**context)

        try:
            glm_result = GlmService.call_glm(
                prompt=prompt,
                response_schema=FOLLOW_UP_SCHEMA,
                max_tokens=4096,
            )
        except GlmServiceError as e:
            logger.error("Follow-up GLM failed for case %d: %s", case_id, e)
            return {"status": "glm_failed", "error": str(e)}

        latest_routing = RoutingDecision.query.filter_by(
            vendor_case_id=case_id
        ).order_by(RoutingDecision.created_at.desc()).first()

        email_draft_data = glm_result.get("email_draft", {})
        draft = EmailDraft(
            vendor_case_id=case_id,
            routing_decision_id=latest_routing.id if latest_routing else None,
            draft_type="vendor_follow_up",
            recipient_email=email_draft_data.get("recipient_email"),
            subject_line=email_draft_data.get("subject_line", "Follow-Up Required — Vendor Registration"),
            body_text=email_draft_data.get("body_text", ""),
        )
        db.session.add(draft)
        db.session.commit()

        return {
            "missing_items_count": len(glm_result.get("missing_items_summary", [])),
            "clarification_requests_count": len(glm_result.get("clarification_requests", [])),
            "email_draft_id": draft.id,
            "ticket_priority": glm_result.get("ticket_priority"),
            "confidence": glm_result.get("confidence"),
        }
