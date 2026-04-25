"""
JSON Schema definitions for validating GLM agent outputs.

Every agent must return structured JSON that passes its corresponding schema
before the output is persisted or passed to the next workflow stage.
"""

# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------

FIELD_STATUS_ENUM = [
    "provided_in_form",
    "matched_with_evidence",
    "mismatched_with_evidence",
    "filled_from_supporting_docs",
    "missing",
    "conflicting",
    "unverifiable",
    "low_confidence",
    "declaration_missing",
]

VALUE_SOURCE_ENUM = [
    "procurement_form",
    "supporting_document",
    "derived_from_multiple_sources",
    "unknown",
]

EVIDENCE_REFERENCE = {
    "type": "object",
    "properties": {
        "source_document_id": {"type": ["integer", "null"]},
        "source_excerpt": {"type": ["string", "null"]},
        "source_type": {"type": "string"},
    },
    "required": ["source_document_id", "source_excerpt", "source_type"],
}

# ---------------------------------------------------------------------------
# Stage 0: Intake Classification
# ---------------------------------------------------------------------------

INTAKE_CLASSIFICATION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "intake_type": {
            "type": "string",
            "enum": ["procurement_form_present", "procurement_form_absent"],
        },
        "form_detected": {"type": "boolean"},
        "form_file_id": {"type": ["integer", "null"]},
        "supporting_document_count": {"type": "integer"},
        "email_content_available": {"type": "boolean"},
        "initial_completeness": {
            "type": "string",
            "enum": ["high", "medium", "low", "unknown"],
        },
        "recommended_next_step": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "intake_type",
        "form_detected",
        "supporting_document_count",
        "initial_completeness",
        "recommended_next_step",
        "confidence",
    ],
}

# ---------------------------------------------------------------------------
# Stage 1: Canonical Form Structuring
# ---------------------------------------------------------------------------

FORM_FIELD_ITEM = {
    "type": "object",
    "properties": {
        "section_name": {"type": "string"},
        "field_name": {"type": "string"},
        "field_label": {"type": "string"},
        "extracted_value": {"type": ["string", "null"]},
        "normalized_value": {"type": ["string", "null"]},
        "value_source": {"type": "string", "enum": VALUE_SOURCE_ENUM},
        "status": {"type": "string", "enum": FIELD_STATUS_ENUM},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_reference": EVIDENCE_REFERENCE,
        "document_sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_document_id": {"type": ["integer", "null"]},
                    "page": {"type": ["integer", "null"]},
                    "extracted_value": {"type": ["string", "null"]},
                    "source_type": {"type": "string"},
                    "source_excerpt": {"type": ["string", "null"]},
                },
                "required": ["source_document_id","extracted_value", "source_type"],
            },
        },
        "notes": {"type": ["string", "null"]},
    },
    "required": [
        "section_name",
        "field_name",
        "field_label",
        "extracted_value",
        "value_source",
        "status",
        "confidence",
    ],
}

CANONICAL_FORM_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "intake_classification": {
            "type": "string",
            "enum": ["procurement_form_present", "procurement_form_absent"],
        },
        "form_artifact_type": {
            "type": "string",
            "enum": ["submitted_form_extraction", "auto_filled_form"],
        },
        "fields": {
            "type": "array",
            "items": FORM_FIELD_ITEM,
        },
        "completeness_report": {
            "type": "object",
            "properties": {
                "total_fields": {"type": "integer"},
                "populated_fields": {"type": "integer"},
                "missing_fields": {"type": "integer"},
                "conflicting_fields": {"type": "integer"},
                "low_confidence_fields": {"type": "integer"},
                "completeness_percentage": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": [
                "total_fields", "populated_fields", "missing_fields",
                "conflicting_fields", "low_confidence_fields", "completeness_percentage",
            ],
        },
        "field_alignment_report": {
            "type": ["object", "null"],
            "properties": {
                "aligned_fields": {"type": "integer"},
                "mismatched_fields": {"type": "integer"},
                "unsupported_fields": {"type": "integer"},
                "mismatches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field_name": {"type": "string"},
                            "form_value": {"type": ["string", "null"]},
                            "evidence_value": {"type": ["string", "null"]},
                            "severity": {"type": "string", "enum": ["minor", "moderate", "critical"]},
                        },
                        "required": ["field_name", "form_value", "evidence_value", "severity"],
                    },
                },
            },
        },
        "recommended_next_step": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "intake_classification",
        "form_artifact_type",
        "fields",
        "completeness_report",
        "recommended_next_step",
        "confidence",
    ],
}

# ---------------------------------------------------------------------------
# Agent 1: Verification
# ---------------------------------------------------------------------------

VERIFICATION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "legitimacy_status": {
            "type": "string",
            "enum": [
                "verified",
                "verified_with_minor_gaps",
                "conflicting",
                "partially_verifiable",
                "unverifiable",
                "suspicious",
            ],
        },
        "mismatch_severity": {
            "type": ["string", "null"],
            "enum": ["minor", "moderate", "critical", None],
        },
        "verified_identity_summary": {"type": "string"},
        "critical_conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["minor", "moderate", "critical"]},
                    "evidence_reference": EVIDENCE_REFERENCE,
                },
                "required": ["field_name", "description", "severity"],
            },
        },
        "missing_critical_fields": {
            "type": "array",
            "items": {"type": "string"},
        },
        "minor_mismatches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["field_name", "description"],
            },
        },
        "moderate_mismatches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["field_name", "description"],
            },
        },
        "critical_mismatches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["field_name", "description"],
            },
        },
        "internal_registry_match_status": {
            "type": ["string", "null"],
            "enum": ["matched", "mismatched", "no_match","new_vendor", "rejected_high_risk_history", None],
        },
        "external_verification_status": {
            "type": ["string", "null"],
            "enum": ["verified", "partially_verified", "unverifiable", "failed", None],
        },
        "summary": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "recommended_next_step": {"type": "string"},
    },
    "required": [
        "legitimacy_status",
        "verified_identity_summary",
        "critical_conflicts",
        "missing_critical_fields",
        "summary",
        "confidence",
        "recommended_next_step",
    ],
}

# ---------------------------------------------------------------------------
# Agent 2: Policy Evaluation
# ---------------------------------------------------------------------------

POLICY_EVALUATION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "risk_band": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "risk_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag_type": {"type": "string"},
                    "description": {"type": "string"},
                    "field_name": {"type": ["string", "null"]},
                    "severity": {"type": "string", "enum": ["informational", "warning", "blocking", "recoverable"]},
                },
                "required": ["flag_type", "description", "severity"],
            },
        },
        "blocking_issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recoverable_issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "blocking_issue_count": {"type": "integer"},
        "recoverable_issue_count": {"type": "integer"},
        "policy_summary": {"type": "string"},
        "policy_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "recommended_next_step": {"type": "string"},
    },
    "required": [
        "risk_band",
        "risk_flags",
        "blocking_issues",
        "recoverable_issues",
        "blocking_issue_count",
        "recoverable_issue_count",
        "policy_summary",
        "policy_confidence",
        "recommended_next_step",
    ],
}

# ---------------------------------------------------------------------------
# Agent 3: Routing
# ---------------------------------------------------------------------------

ROUTING_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "decision_code": {
            "type": "string",
            "enum": [
                "complete_verified",
                "follow_up_required",
                "flagged_high_risk",
                "rejected",
                "manual_review_required",
            ],
        },
        "decision_reasoning_summary": {"type": "string"},
        "decision_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "finding": {"type": "string"},
                },
                "required": ["source", "finding"],
            },
        },
        "recommended_procurement_action": {"type": "string"},
        "required_follow_up": {"type": "boolean"},
        "next_workflow_state": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "decision_code",
        "decision_reasoning_summary",
        "recommended_procurement_action",
        "next_workflow_state",
        "confidence",
    ],
}

# ---------------------------------------------------------------------------
# Agent 4: Ticketing / Follow-Up
# ---------------------------------------------------------------------------

FOLLOW_UP_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "missing_items_summary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "field_label": {"type": "string"},
                    "issue_type": {
                        "type": "string",
                        "enum": ["missing_field", "missing_document", "conflicting_value", "low_confidence", "clarification_needed"],
                    },
                    "description": {"type": "string"},
                },
                "required": ["field_name", "field_label", "issue_type", "description"],
            },
        },
        "clarification_requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["topic", "question"],
            },
        },
        "email_draft": {
            "type": "object",
            "properties": {
                "subject_line": {"type": "string"},
                "body_text": {"type": "string"},
                "recipient_email": {"type": ["string", "null"]},
            },
            "required": ["subject_line", "body_text"],
        },
        "ticket_priority": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "missing_items_summary",
        "clarification_requests",
        "email_draft",
        "ticket_priority",
        "confidence",
    ],
}

# ---------------------------------------------------------------------------
# Lookup: map stage name to schema
# ---------------------------------------------------------------------------

AGENT_OUTPUT_SCHEMAS = {
    "intake_classification": INTAKE_CLASSIFICATION_SCHEMA,
    "canonical_form": CANONICAL_FORM_SCHEMA,
    "verification": VERIFICATION_SCHEMA,
    "policy_evaluation": POLICY_EVALUATION_SCHEMA,
    "routing": ROUTING_SCHEMA,
    "follow_up": FOLLOW_UP_SCHEMA,
}
