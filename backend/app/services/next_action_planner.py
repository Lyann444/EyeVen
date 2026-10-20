import json

from app.models import (
    EmailDraft,
    EyeVenFormFieldValue,
    PolicyEvaluationRun,
    RoutingDecision,
    UploadedFile,
    VendorCase,
    VerificationRun,
)
from app.prompts.next_action_planner import NEXT_ACTION_PROMPT
from app.services.audit_service import AuditService
from app.services.glm_service import GlmService
from app.validators.agent_schemas import (
    CANONICAL_FIELD_NAMES,
    NEXT_ACTION_SCHEMA,
)

TARGETABLE_FIELDS = {"company_registered_number", "ssm_roc_rob_number"}
SUPPORTED_STAGES = {
    "extraction",
    "form_structuring",
    "verification",
    "policy_evaluation",
}


class NextActionPlannerError(Exception):
    pass


class NextActionPlanner:
    @staticmethod
    def plan(
        case_id: int,
        prior_actions: list[dict],
        snapshot: dict | None = None,
    ) -> dict:
        if snapshot is None:
            snapshot = NextActionPlanner.case_snapshot(case_id, prior_actions)
        result = GlmService.call_glm(
            prompt=NEXT_ACTION_PROMPT.format(
                case_snapshot=json.dumps(snapshot, indent=2)
            ),
            response_schema=NEXT_ACTION_SCHEMA,
            max_tokens=1024,
        )
        return NextActionPlanner.validate_plan(result)

    @staticmethod
    def validate_plan(plan: dict) -> dict:
        if not isinstance(plan, dict):
            raise NextActionPlannerError("Planner response must be an object")
        if set(plan) != {"action", "reason", "target_fields", "target_stage"}:
            raise NextActionPlannerError("Planner response has missing or unexpected properties")

        action = plan.get("action")
        target_fields = plan.get("target_fields")
        target_stage = plan.get("target_stage")
        if not isinstance(target_fields, list) or any(
            not isinstance(field, str) or field not in CANONICAL_FIELD_NAMES
            for field in target_fields
        ) or len(set(target_fields)) != len(target_fields):
            raise NextActionPlannerError("Planner target fields are invalid")
        if action == "targeted_verification":
            if not target_fields or not set(target_fields).issubset(TARGETABLE_FIELDS):
                raise NextActionPlannerError(
                    "Targeted verification requires supported registration fields"
                )
            if target_stage is not None:
                raise NextActionPlannerError(
                    "Targeted verification cannot specify a target stage"
                )
        elif action == "reprocess_stage":
            if (
                not isinstance(target_stage, str)
                or target_stage not in SUPPORTED_STAGES
                or target_fields
            ):
                raise NextActionPlannerError(
                    "Reprocessing requires one supported stage and no target fields"
                )
        elif action in {
            "request_information",
            "manual_review",
            "finish",
        }:
            if target_fields or target_stage is not None:
                raise NextActionPlannerError(
                    f"{action} does not accept target fields or a target stage"
                )
        else:
            raise NextActionPlannerError(f"Unsupported planner action: {action!r}")

        reason = plan.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise NextActionPlannerError("Planner reason must be a non-empty string")
        return {
            "action": action,
            "reason": reason.strip(),
            "target_fields": target_fields,
            "target_stage": target_stage,
        }

    @staticmethod
    def case_snapshot(case_id: int, prior_actions: list[dict]) -> dict:
        case = VendorCase.query.get_or_404(case_id)
        fields = EyeVenFormFieldValue.query.filter_by(vendor_case_id=case_id).all()
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
        routing = (
            RoutingDecision.query.filter_by(vendor_case_id=case_id)
            .order_by(RoutingDecision.created_at.desc())
            .first()
        )
        email_draft_count = EmailDraft.query.filter_by(
            vendor_case_id=case_id
        ).count()
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()
        risk_flags = AuditService._parse_json_list(policy.risk_flags) if policy else []

        return {
            "case_id": case_id,
            "current_state": case.current_state,
            "terminal": case.current_state in {
                "completed", "rejected", "flagged_high_risk",
            },
            "fields": [
                {
                    "field_name": field.field_name,
                    "status": field.status,
                    "value_present": bool(
                        (field.normalized_value_text or field.value_text or "").strip()
                    ),
                    "evidence_available": bool(
                        field.source_file_id or (field.source_excerpt or "").strip()
                    ),
                }
                for field in fields
            ],
            "available_evidence": [
                {
                    "file_id": file.id,
                    "file_type": file.file_type,
                    "source_category": file.source_category,
                    "upload_status": file.upload_status,
                }
                for file in files
            ],
            "verification": {
                "legitimacy_status": verification.legitimacy_status,
                "mismatch_severity": verification.mismatch_severity,
                "summary": verification.summary,
                "critical_conflicts": [
                    {
                        "field_name": item.get("field_name"),
                        "severity": item.get("severity"),
                    }
                    for item in AuditService._parse_json_list(
                        verification.key_findings
                    )
                    if isinstance(item, dict)
                ],
            } if verification else None,
            "policy": {
                "risk_band": policy.risk_band,
                "blocking_issue_count": policy.blocking_issue_count,
                "recoverable_issue_count": policy.recoverable_issue_count,
                "summary": policy.policy_summary,
                "risk_flags": [
                    {
                        "flag_type": item.get("flag_type"),
                        "field_name": item.get("field_name"),
                        "severity": item.get("severity"),
                    }
                    for item in risk_flags
                    if isinstance(item, dict)
                ],
            } if policy else None,
            "routing_decision": routing.decision_code if routing else None,
            "follow_up_draft_count": email_draft_count,
            "prior_actions": prior_actions,
        }
