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
)
from app.orchestrators.extraction_orchestrator import ExtractionOrchestrator
from app.services.form_service import FormService
from app.services.glm_service import GlmService, GlmServiceError
from app.services.context_assembler import ContextAssembler
from app.services.safety_gate import SafetyGate
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

        # Routing only runs after policy evaluation has successfully completed.
        if case.current_state == "routing_in_progress":
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

        if case.current_state == "pending_follow_up":
            state_before = case.current_state
            stage_started_at = datetime.utcnow()
            stage_started_clock = time.perf_counter()
            follow_up_result = WorkflowOrchestrator._run_follow_up(case_id)
            results["follow_up"] = follow_up_result
            case = VendorCase.query.get(case_id)
            trace.append(WorkflowOrchestrator._trace_stage(
                case_id, "follow_up", state_before, case.current_state, follow_up_result,
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
            retryable_stages = {
                "extraction",
                "form_structuring",
                "verification",
                "policy_evaluation",
            }
            failed_stages = [
                {
                    "action": "reprocess_stage",
                    "target_stage": stage,
                    "target_fields": [],
                    "outcome": stage_result.get("status"),
                    "progress": False,
                    "reason": f"Prior {stage} stage returned {stage_result.get('status')}.",
                }
                for stage, stage_result in results.items()
                if stage in retryable_stages
                and isinstance(stage_result, dict)
                and stage_result.get("status") in {"failed", "glm_failed"}
            ]
            if failed_stages and case.current_state in {
                "manual_review_required", "verification_blocked",
            }:
                state_before = case.current_state
                case.current_state = "processing_failed"
                db.session.commit()
                trace.append(WorkflowOrchestrator._trace_stage(
                    case_id, "processing_failure_recovery", state_before,
                    case.current_state, {"status": "retry_available"},
                ))
            agentic_trace, stopping_reason = WorkflowOrchestrator._run_agentic_loop(
                case_id, initial_actions=failed_stages
            )
            result["agentic_trace"] = agentic_trace
            result["stopping_reason"] = stopping_reason
            case = VendorCase.query.get(case_id)
            result["current_state"] = case.current_state
        return result

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
    def _snapshot_fingerprint(snapshot):
        return json.dumps({
            "current_state": snapshot["current_state"],
            "fields": snapshot["fields"],
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
            "external_verification_performed",
            "stage",
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
        failures = [
            {
                "file_id": item.get("file_id"),
                "status": item.get("status", "unknown"),
            }
            for item in extraction_results
            if item.get("status") != "completed"
        ]
        return {
            "status": "failed" if failures else "completed",
            "current_state": result.get("current_state"),
            "files_processed": result.get("files_processed", 0),
            "zip_files_extracted": result.get("zip_files_extracted", 0),
            "failures": failures,
        }

    @staticmethod
    def _has_unresolved_issues(snapshot):
        unresolved_statuses = {
            "missing", "conflicting", "unverifiable",
            "low_confidence", "declaration_missing",
            "mismatched_with_evidence",
        }
        if any(field["status"] in unresolved_statuses for field in snapshot["fields"]):
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
            return {"status": "glm_failed", "error": str(e)}

        run = VerificationRun(
            vendor_case_id=case_id,
            run_status="completed",
            legitimacy_status=glm_result.get("legitimacy_status"),
            mismatch_severity=glm_result.get("mismatch_severity"),
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
            "mismatch_severity": glm_result.get("mismatch_severity"),
            "confidence": glm_result.get("confidence"),
            "summary": glm_result.get("summary"),
        }

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
            return {"status": "glm_failed", "error": str(e)}

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
            return {"status": "glm_failed", "error": str(e)}

        decision_code = glm_result.get("decision_code")
        decision_reasoning = glm_result.get("decision_reasoning_summary", "")
        recommended_action = glm_result.get("recommended_procurement_action")
        decision_evidence = WorkflowOrchestrator._normalize_decision_evidence(
            glm_result.get("decision_evidence", [])
        )

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

        latest_verification = VerificationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(VerificationRun.created_at.desc()).first()
        latest_policy = PolicyEvaluationRun.query.filter_by(
            vendor_case_id=case_id
        ).order_by(PolicyEvaluationRun.created_at.desc()).first()

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
