import json
import logging
from datetime import datetime

from app.extensions import db
from app.models import (
    VendorCase,
    EyeVenFormFieldValue,
    VerificationRun,
    PolicyEvaluationRun,
    RoutingDecision,
    EmailDraft,
)
from app.services.form_service import FormService
from app.services.glm_service import GlmService, GlmServiceError
from app.services.context_assembler import ContextAssembler
from app.services.safety_gate import SafetyGate
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
    def run(case_id):
        case = VendorCase.query.get_or_404(case_id)

        if case.current_state in ("completed", "rejected", "flagged_high_risk"):
            logger.info("Case %d already in terminal state: %s", case_id, case.current_state)
            return {"case_id": case_id, "status": "already_terminal", "current_state": case.current_state}

        results = {}

        if case.current_state in ("intake_classified", "procurement_form_detected", "procurement_form_missing",
                                   "intake_received", "uploaded", "extracting"):
            form_result = FormService.structure_form(case_id)
            results["form_structuring"] = form_result
            case = VendorCase.query.get(case_id)

        if case.current_state in ("field_alignment_completed", "incomplete_after_autofill",
                                   "form_comparison_in_progress", "form_auto_fill_in_progress"):
            ver_result = WorkflowOrchestrator._run_verification(case_id)
            results["verification"] = ver_result
            case = VendorCase.query.get(case_id)

        # Verification and policy evaluation use distinct states so the
        # workflow cannot accidentally skip policy evaluation after verification.
        # Re-running from policy_review_in_progress retries policy evaluation
        # instead of routing with potentially stale policy results.
        if case.current_state in ("verification_completed", "policy_review_in_progress"):
            policy_result = WorkflowOrchestrator._run_policy_evaluation(case_id)
            results["policy_evaluation"] = policy_result
            case = VendorCase.query.get(case_id)

        # Routing only runs after policy evaluation has successfully completed.
        if case.current_state == "routing_in_progress":
            routing_result = WorkflowOrchestrator._run_routing(case_id)
            results["routing"] = routing_result
            case = VendorCase.query.get(case_id)

        if case.current_state == "pending_follow_up":
            follow_up_result = WorkflowOrchestrator._run_follow_up(case_id)
            results["follow_up"] = follow_up_result
            case = VendorCase.query.get(case_id)

        return {
            "case_id": case_id,
            "current_state": case.current_state,
            "stages_run": list(results.keys()),
            "results": results,
        }

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

        # The LLM may recommend approval, but only deterministic Python rules
        # can allow the case to enter the completed state.
        safety_gate_result = None
        if decision_code == "complete_verified":
            safety_gate_result = SafetyGate.evaluate(case_id)
            if not safety_gate_result["allowed"]:
                decision_code = safety_gate_result["suggested_decision"]
                reasons = ", ".join(safety_gate_result["reasons"])
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
            "confidence": glm_result.get("confidence"),
            "next_state": case.current_state,
            "safety_gate": safety_gate_result,
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
