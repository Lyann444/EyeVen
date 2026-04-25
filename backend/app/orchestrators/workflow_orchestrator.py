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
    VendorRegistry
)
from app.services.form_service import FormService
from app.services.glm_service import GlmService, GlmServiceError
from app.services.context_assembler import ContextAssembler
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
                                   "intake_received", "uploaded", "extracting", "verification_blocked"):
            # Reset blocked state so form structuring can retry
            if case.current_state == "verification_blocked":
                case.current_state = "intake_classified"
                db.session.commit()
            form_result = FormService.structure_form(case_id)
            results["form_structuring"] = form_result
            case = VendorCase.query.get(case_id)

        if case.current_state in ("field_alignment_completed", "incomplete_after_autofill",
                                   "form_comparison_in_progress", "form_auto_fill_in_progress"):
            ver_result = WorkflowOrchestrator._run_verification(case_id)
            results["verification"] = ver_result
            case = VendorCase.query.get(case_id)

        # 2. Trigger Policy Evaluation (Waiting for Verification to finish)
        if case.current_state in ("policy_review_in_progress",):
            policy_result = WorkflowOrchestrator._run_policy_evaluation(case_id)
            results["policy_evaluation"] = policy_result
            case = VendorCase.query.get(case_id)

        # 3. Trigger Routing (Waiting for Policy Evaluation to finish)
        if case.current_state in ("routing_in_progress",):
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
            blocking_issues = glm_result.get("blocking_issues", []) or []
            recoverable_issues = glm_result.get("recoverable_issues", []) or []

            glm_result["blocking_issue_count"] = len(blocking_issues)
            glm_result["recoverable_issue_count"] = len(recoverable_issues)
            seen_flags = set()
            deduped_flags = []

            for flag in glm_result.get("risk_flags", []) or []:
                key = (
                    flag.get("flag_type"),
                    flag.get("field_name"),
                    flag.get("severity"),
                )

                if key not in seen_flags:
                    seen_flags.add(key)
                    deduped_flags.append(flag)

            glm_result["risk_flags"] = deduped_flags
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
        case.current_state = "policy_review_in_progress"
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
            decision_code=glm_result.get("decision_code"),
            recommended_procurement_action=glm_result.get("recommended_procurement_action"),
            decision_reasoning_summary=glm_result.get("decision_reasoning_summary"),
            confidence_score=glm_result.get("confidence"),
        )
        db.session.add(decision)

        decision_code = glm_result.get("decision_code")
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

        # =========================================================
        # HACKATHON: AUTO-SAVE NEW VENDORS TO INTERNAL REGISTRY
        # =========================================================
        if decision_code in ("complete_verified", "rejected", "flagged_high_risk"):
            import random
            
            # 1. Grab all extracted fields from the form
            fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
            field_map = {f.field_name: f.value_text or "" for f in fields}

            ssm_number = field_map.get("ssm_roc_rob_number", "").strip()
            company_name = field_map.get("company_registered_name", "").strip()

            # 2. Proceed only if we have minimum identifying data
            if ssm_number or company_name:
                
                # Check if they somehow already exist to prevent database crashes
                existing_vendor = None
                if ssm_number:
                    existing_vendor = VendorRegistry.query.filter_by(ssm_roc_rob_number=ssm_number).first()
                elif company_name:
                    existing_vendor = VendorRegistry.query.filter_by(company_registered_name=company_name).first()

                # 3. If they are a new vendor, save them for future checks
                if not existing_vendor:
                    # 'complete_verified' = 0 risk. Everything else = 1 risk.
                    is_high_risk = 0 if decision_code == "complete_verified" else 1
                    reason = glm_result.get("decision_reasoning_summary") if is_high_risk else ""
                    
                    # Generate a random V-number for the hackathon demo
                    new_vendor_id = f"V-3{random.randint(100, 999)}"

                    new_vendor = VendorRegistry(
                        vendor_number=new_vendor_id,
                        ssm_roc_rob_number=ssm_number,
                        company_registered_name=company_name,
                        company_registered_number=field_map.get("company_registered_number", ""),
                        tax_sst_gst_number=field_map.get("tax_sst_gst_number", ""),
                        company_email=field_map.get("company_email", ""),
                        contact_person_name=field_map.get("contact_person_name", ""),
                        business_type=field_map.get("business_type", ""),
                        is_past_high_risk=is_high_risk,
                        high_risk_reason=reason
                    )
                    db.session.add(new_vendor)
                    db.session.commit()
                    logger.info(f"Auto-saved new vendor {company_name} to internal registry. High Risk: {bool(is_high_risk)}")
        # =========================================================

        return {
            "decision_code": decision_code,
            "reasoning": glm_result.get("decision_reasoning_summary"),
            "confidence": glm_result.get("confidence"),
            "next_state": case.current_state,
        }

    @staticmethod
    def _run_follow_up(case_id):
        case = VendorCase.query.get(case_id)
        if case.current_state in ("completed", "rejected", "flagged_high_risk"):
            logger.info("Skipping follow-up for case %d as it is in terminal state: %s", case_id, case.current_state)
            return {
                "status": "skipped", 
                "message": f"Follow-up is not required for cases in '{case.current_state}' state."
            }
        # --- END GUARD CLAUSE ---
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
