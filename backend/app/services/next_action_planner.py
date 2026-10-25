import json

from app.models import (
    EmailDraft,
    EyeVenFormFieldValue,
    PolicyEvaluationRun,
    RoutingDecision,
    ReprocessingRun,
    UploadedFile,
    VendorCase,
    VerificationRun,
)
from app.prompts.next_action_planner import NEXT_ACTION_PROMPT
from app.services.audit_service import AuditService
from app.services.glm_service import GlmService
from app.services.safety_gate import FOLLOW_UP_FIELDS, SafetyGate
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
    "routing",
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
        try:
            validated = NextActionPlanner.validate_plan(result)
            NextActionPlanner.validate_plan_for_case(validated, snapshot)
            return validated
        except NextActionPlannerError as plan_error:
            fallback = NextActionPlanner._evidence_based_plan(snapshot)
            if fallback is None:
                raise plan_error
            validated_fallback = NextActionPlanner.validate_plan(fallback)
            NextActionPlanner.validate_plan_for_case(validated_fallback, snapshot)
            return validated_fallback

    @staticmethod
    def _evidence_based_plan(snapshot: dict) -> dict | None:
        fields = snapshot.get("fields", [])
        unresolved = [
            field for field in fields
            if field.get("follow_up_eligible")
            and (
                not field.get("value_present")
                or field.get("status") in {
                    "missing",
                    "conflicting",
                    "unverifiable",
                    "low_confidence",
                    "declaration_missing",
                    "mismatched_with_evidence",
                }
            )
        ]
        failed_stages = [
            action.get("target_stage")
            for action in snapshot.get("prior_actions", [])
            if action.get("action") == "reprocess_stage"
            and action.get("outcome") in {"failed", "glm_failed"}
            and action.get("target_stage") in SUPPORTED_STAGES
        ]
        for stage in failed_stages:
            return NextActionPlanner._plan(
                "reprocess_stage",
                f"Retry the failed {stage} stage before taking another action.",
                target_stage=stage,
            )

        if snapshot.get("case_evidence_assessment", {}).get(
            "documents_added_after_last_structuring"
        ):
            return NextActionPlanner._plan(
                "reprocess_stage",
                "A new document is available and must be extracted and reviewed.",
                target_stage="extraction",
            )

        if not unresolved:
            policy = snapshot.get("policy")
            verification = snapshot.get("verification")
            if (
                (policy and (
                    policy.get("risk_band") in {"high", "critical"}
                    or policy.get("blocking_issue_count", 0) > 0
                ))
                or (verification and verification.get("mismatch_severity") in {
                    "moderate", "critical"
                })
            ):
                return NextActionPlanner._plan(
                    "manual_review",
                    "No recoverable information remains, but unresolved risk requires review.",
                )
            return NextActionPlanner._plan(
                "finish",
                "No eligible unresolved fields or failed stages remain.",
            )

        evidence_assessment = snapshot.get("case_evidence_assessment", {})
        if evidence_assessment.get("ambiguous_fields"):
            return NextActionPlanner._plan(
                "manual_review",
                "The case contains contradictory or ambiguous evidence that needs human review.",
            )

        unchanged_stages = set(snapshot.get("unchanged_successful_stages", []))
        if not snapshot.get("available_evidence"):
            return NextActionPlanner._plan(
                "request_pic_upload",
                "No documents are attached; ask the PIC to upload the available case documents.",
            )

        if evidence_assessment.get("documents_added_after_last_structuring"):
            return NextActionPlanner._plan(
                "reprocess_stage",
                "Newly added documents must be extracted before follow-up is considered.",
                target_stage="extraction",
            )

        unresolved_with_evidence = [
            field for field in unresolved if field.get("evidence_available")
        ]
        if unresolved_with_evidence:
            stage = (
                "verification"
                if any("PIC manual correction;" in field.get("notes", "")
                       for field in unresolved_with_evidence)
                else "form_structuring"
            )
            if stage not in unchanged_stages:
                return NextActionPlanner._plan(
                    "reprocess_stage",
                    "Existing field evidence is available; refresh its interpretation before requesting it again.",
                    target_stage=stage,
                )
            return NextActionPlanner._plan(
                "manual_review",
                "The field remains unresolved after processing unchanged available evidence.",
            )

        availability = {field.get("availability") for field in unresolved}
        if "documents_not_yet_reviewed" in availability:
            stage = "extraction"
            if stage not in unchanged_stages:
                return NextActionPlanner._plan(
                    "reprocess_stage",
                    "Submitted documents have not yet been reviewed for the missing information.",
                    target_stage=stage,
                )
            return NextActionPlanner._plan(
                "manual_review",
                "Submitted documents remain unreviewed, but extraction cannot be repeated without changed inputs.",
            )

        if availability == {"not_found_in_reviewed_documents"}:
            return NextActionPlanner._plan(
                "request_information",
                "Required information was not found after reviewing the submitted documents.",
            )

        return NextActionPlanner._plan(
            "manual_review",
            "Case evidence does not establish whether the missing information can be recovered or must be requested.",
        )

    @staticmethod
    def _plan(action, reason, target_stage=None):
        return {
            "action": action,
            "reason": reason,
            "target_fields": [],
            "target_stage": target_stage,
        }

    @staticmethod
    def validate_plan_for_case(plan: dict, snapshot: dict) -> None:
        fields = snapshot.get("fields", [])
        unresolved = [
            field for field in fields
            if field.get("follow_up_eligible")
            and (
                not field.get("value_present")
                or field.get("status") in {
                    "missing",
                    "conflicting",
                    "unverifiable",
                    "low_confidence",
                    "declaration_missing",
                    "mismatched_with_evidence",
                }
            )
        ]
        if plan["action"] == "finish" and unresolved:
            raise NextActionPlannerError(
                "Planner cannot finish while eligible fields remain unresolved"
            )

        if plan["action"] == "request_information":
            if any(
                field.get("evidence_available")
                or field.get("availability") != "not_found_in_reviewed_documents"
                for field in unresolved
            ):
                raise NextActionPlannerError(
                    "Vendor follow-up is only allowed after case documents have "
                    "been reviewed and the requested information was not found"
                )
            if not unresolved:
                raise NextActionPlannerError(
                    "Vendor follow-up requires unresolved eligible fields"
                )

        if plan["action"] == "request_pic_upload":
            if snapshot.get("available_evidence"):
                raise NextActionPlannerError(
                    "PIC document upload is only allowed when no case documents exist"
                )
            if not unresolved:
                raise NextActionPlannerError(
                    "PIC document upload requires unresolved eligible fields"
                )

        if plan["action"] == "reprocess_stage":
            stage = plan["target_stage"]
            failed_stages = {
                action.get("target_stage")
                for action in snapshot.get("prior_actions", [])
                if action.get("action") == "reprocess_stage"
                and action.get("outcome") in {"failed", "glm_failed"}
            }
            if (
                stage in snapshot.get("unchanged_successful_stages", [])
                and stage not in failed_stages
            ):
                raise NextActionPlannerError(
                    f"Cannot reprocess {stage}: inputs are unchanged since its last successful run"
                )

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
            "request_pic_upload",
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
        field_values = {
            item.field_name: SafetyGate._field_value(item)
            for item in fields
        }
        files_by_id = {file.id: file for file in files}
        latest_file_upload = max(
            (file.uploaded_at for file in files if file.uploaded_at),
            default=None,
        )
        latest_field_update = max(
            (field.updated_at for field in fields if field.updated_at),
            default=None,
        )
        documents_added_after_structuring = bool(
            latest_file_upload
            and latest_field_update
            and latest_file_upload > latest_field_update
        )
        documents_have_been_reviewed = case.current_state in {
            "field_alignment_completed",
            "incomplete_after_autofill",
            "form_comparison_in_progress",
            "form_auto_fill_in_progress",
            "verification_completed",
            "verification_blocked",
            "policy_review_in_progress",
            "policy_evaluated",
            "routing_in_progress",
            "pending_follow_up",
            "manual_review_required",
            "completed",
            "rejected",
            "flagged_high_risk",
        }

        unchanged_successful_stages = set()
        completed_reprocessing_runs = ReprocessingRun.query.filter_by(
            vendor_case_id=case_id,
            run_status="completed",
        ).all()
        if completed_reprocessing_runs:
            from app.orchestrators.reprocessing_orchestrator import (
                ReprocessingOrchestrator,
            )

            current_fingerprints = {
                stage: ReprocessingOrchestrator._input_fingerprint(case_id, stage)
                for stage in SUPPORTED_STAGES
            }
            for run in completed_reprocessing_runs:
                try:
                    metadata = json.loads(run.reason_summary or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                stage = metadata.get("stage") if isinstance(metadata, dict) else None
                if (
                    stage in current_fingerprints
                    and metadata.get("input_fingerprint")
                    == current_fingerprints[stage]
                ):
                    unchanged_successful_stages.add(stage)
        if not documents_added_after_structuring:
            for action in prior_actions:
                result = action.get("result")
                stage = action.get("target_stage")
                if (
                    action.get("action") == "reprocess_stage"
                    and action.get("outcome") in {"completed", "skipped_unchanged"}
                    and stage in SUPPORTED_STAGES
                    and isinstance(result, dict)
                    and not result.get("result_changed", False)
                    and not result.get("error")
                ):
                    unchanged_successful_stages.add(stage)

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
                    "value": field.value_text,
                    "value_present": bool(
                        (field.normalized_value_text or field.value_text or "").strip()
                    ),
                    "evidence_available": bool(
                        field.source_file_id or (field.source_excerpt or "").strip()
                    ),
                    "source_file_name": (
                        files_by_id[field.source_file_id].original_filename
                        if field.source_file_id in files_by_id
                        else None
                    ),
                    "source_excerpt": (field.source_excerpt or "")[:1200],
                    "notes": (field.notes or "")[:1200],
                    "availability": NextActionPlanner._field_availability(
                        field,
                        files_by_id,
                        documents_added_after_structuring,
                        documents_have_been_reviewed,
                    ),
                    "follow_up_eligible": SafetyGate.is_follow_up_needed(
                        field, field_values
                    ),
                }
                for field in fields
            ],
            "follow_up_candidates": [
                {
                    "field_name": field.field_name,
                    "status": field.status,
                    "availability": NextActionPlanner._field_availability(
                        field,
                        files_by_id,
                        documents_added_after_structuring,
                        documents_have_been_reviewed,
                    ),
                }
                for field in fields
                if SafetyGate.is_follow_up_needed(
                    field, field_values
                )
            ],
            "available_evidence": [
                {
                    "file_id": file.id,
                    "file_type": file.file_type,
                    "source_category": file.source_category,
                    "upload_status": file.upload_status,
                    "original_filename": file.original_filename,
                }
                for file in files
            ],
            "case_evidence_assessment": {
                "document_count": len(files),
                "documents_added_after_last_structuring": documents_added_after_structuring,
                "documents_reviewed": documents_have_been_reviewed,
                "unresolved_fields_with_evidence": [
                    field.field_name
                    for field in fields
                    if SafetyGate.is_follow_up_needed(field, field_values)
                    and (
                        field.source_file_id
                        or (field.source_excerpt or "").strip()
                    )
                ],
                "ambiguous_fields": [
                    field.field_name
                    for field in fields
                    if SafetyGate.is_follow_up_needed(field, field_values)
                    and field.status in {
                        "conflicting",
                        "unverifiable",
                        "mismatched_with_evidence",
                    }
                ],
                "failed_stages": [
                    {
                        "stage": action.get("target_stage"),
                        "outcome": action.get("outcome"),
                    }
                    for action in prior_actions
                    if action.get("action") == "reprocess_stage"
                    and action.get("outcome") in {"failed", "glm_failed"}
                ],
            },
            "unchanged_successful_stages": sorted(unchanged_successful_stages),
            "verification": {
                "legitimacy_status": verification.legitimacy_status,
                "mismatch_severity": verification.mismatch_severity,
                "run_status": verification.run_status,
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

    @staticmethod
    def _field_availability(
        field,
        files_by_id,
        documents_added_after_structuring,
        documents_have_been_reviewed,
    ):
        if field.source_file_id or (field.source_excerpt or "").strip():
            return "evidence_found_for_field"
        if documents_added_after_structuring:
            return "new_document_needs_processing"
        if not files_by_id:
            return "no_documents_submitted"
        if not documents_have_been_reviewed:
            return "documents_not_yet_reviewed"
        if field.status in {"conflicting", "unverifiable", "mismatched_with_evidence"}:
            return "ambiguous_or_conflicting"
        return "not_found_in_reviewed_documents"
