import json
import logging

from flask import current_app

from app.extensions import db
from app.models import VendorCase, UploadedFile, EyeVenFormFieldValue
from app.services.glm_service import GlmService, GlmServiceError, GlmSchemaValidationError
from app.prompts.canonical_form import CANONICAL_FORM_PROMPT
from app.validators.agent_schemas import CANONICAL_FORM_SCHEMA

logger = logging.getLogger(__name__)


class FormService:
    @staticmethod
    def structure_form(case_id):
        case = VendorCase.query.get_or_404(case_id)

        if case.intake_type == "procurement_form_present":
            case.current_state = "form_comparison_in_progress"
        else:
            case.current_state = "form_auto_fill_in_progress"
        db.session.commit()

        context = FormService._build_context(case_id)

        prompt = CANONICAL_FORM_PROMPT.format(**context)

        try:
            glm_result = GlmService.call_glm(
                prompt=prompt,
                response_schema=CANONICAL_FORM_SCHEMA,
                max_tokens=16384,
                temperature=0.1,
            )
        except (GlmServiceError, GlmSchemaValidationError) as e:
            logger.error("Canonical form GLM call failed for case %d: %s", case_id, e)
            case.current_state = "verification_blocked"
            db.session.commit()
            return {"case_id": case_id, "status": "glm_failed", "error": str(e)}

        FormService._persist_form_fields(case_id, glm_result)

        completeness = glm_result.get("completeness_report", {})

        if case.intake_type == "procurement_form_present":
            case.current_state = "field_alignment_completed"
        else:
            populated = completeness.get("populated_fields", 0)
            missing = completeness.get("missing_fields", 0)
            if missing > 0 and populated > 0:
                case.current_state = "incomplete_after_autofill"
            elif populated == 0:
                case.current_state = "incomplete_after_autofill"
            else:
                case.current_state = "field_alignment_completed"

        db.session.commit()

        return {
            "case_id": case_id,
            "intake_classification": glm_result.get("intake_classification"),
            "form_artifact_type": glm_result.get("form_artifact_type"),
            "completeness_report": completeness,
            "field_alignment_report": glm_result.get("field_alignment_report"),
            "recommended_next_step": glm_result.get("recommended_next_step"),
            "confidence": glm_result.get("confidence"),
            "current_state": case.current_state,
        }

    @staticmethod
    def extract_form_fields(case_id):
        return FormService.structure_form(case_id)

    @staticmethod
    def reconstruct_form(case_id, extracted_facts=None):
        return FormService.structure_form(case_id)

    @staticmethod
    def compare_form_to_evidence(case_id):
        return FormService.structure_form(case_id)

    @staticmethod
    def _build_context(case_id):
        case = VendorCase.query.get_or_404(case_id)
        files = UploadedFile.query.filter_by(vendor_case_id=case_id).all()

        extracted_parts = []
        for f in files:
            content = FormService._read_extracted_text(f)
            if content:
                extracted_parts.append({
                    "file_id": f.id,
                    "filename": f.original_filename,
                    "source_category": f.source_category,
                    "extracted_text": content,
                })

        extracted_content = json.dumps(extracted_parts, indent=2)

        return {
            "intake_classification": case.intake_type,
            "extracted_content": extracted_content,
            "email_content": "No email content provided",
        }

    @staticmethod
    def _read_extracted_text(uploaded_file):
        file_path = uploaded_file.storage_path

        if not file_path:
            return None

        from app.services.parser_service import ParserService
        from app.services.ocr_service import OcrService
        import os

        ext = uploaded_file.file_type.lower()

        if not os.path.isfile(file_path):
            return None

        if ext in ("csv", "xlsx", "xls"):
            parsed = ParserService.parse_file(file_path)
            if parsed.get("status") == "completed":
                if ext == "csv":
                    rows = parsed.get("rows", [])
                    return json.dumps(rows[:50])
                else:
                    return json.dumps(parsed.get("sheets", {}))
            return None

        if ext in ("pdf", "png", "jpg", "jpeg", "tiff", "bmp"):
            ocr_result = OcrService.run_ocr(file_path)

            blocks_by_page = {}
            for block in ocr_result.get("text_blocks", []):
                page = block.get("page", 1)
                text = block.get("text", "").strip()
                if text:
                    blocks_by_page.setdefault(page, []).append(text)

            page_texts = []
            for page in sorted(blocks_by_page.keys()):
                page_texts.append(
                    f"[PAGE {page}]\n" + "\n".join(blocks_by_page[page])
                )

            return "\n\n".join(page_texts)

        return None

    @staticmethod
    def _persist_form_fields(case_id, glm_result):
        fields = glm_result.get("fields", [])

        for field_data in fields:
            field_name = field_data.get("field_name")
            if not field_name:
                continue

            # Build document_values from GLM document_sources
            doc_vals = {}
            for ds in (field_data.get("document_sources") or []):
                doc_id = ds.get("source_document_id")
                page = ds.get("page")
                value = ds.get("extracted_value")

                if doc_id is not None and value:
                    if page is not None:
                        key = f"file_{doc_id}_page_{page}"
                    else:
                        key = f"file_{doc_id}"

                    doc_vals[key] = value

            evidence = field_data.get("evidence_reference") or {}

            # Fallback if GLM does not return document_sources
            if not doc_vals and evidence.get("source_document_id") and field_data.get("extracted_value"):
                doc_vals = {
                    f"file_{evidence['source_document_id']}": field_data["extracted_value"]
                }

            existing = EyeVenFormFieldValue.query.filter_by(
                vendor_case_id=case_id,
                field_name=field_name
            ).first()

            if existing:
                existing.value_text = field_data.get("extracted_value")
                existing.normalized_value_text = field_data.get("normalized_value")
                existing.value_source = field_data.get("value_source", "unknown")
                existing.status = field_data.get("status", "missing")
                existing.confidence_score = field_data.get("confidence")
                existing.notes = field_data.get("notes")
                existing.source_file_id = evidence.get("source_document_id")
                existing.source_excerpt = evidence.get("source_excerpt")
                existing.document_values = doc_vals if doc_vals else None

            else:
                existing = EyeVenFormFieldValue(
                    vendor_case_id=case_id,
                    section_name=field_data.get("section_name", "unknown"),
                    field_name=field_name,
                    field_label=field_data.get("field_label", field_name),
                    value_text=field_data.get("extracted_value"),
                    normalized_value_text=field_data.get("normalized_value"),
                    value_source=field_data.get("value_source", "unknown"),
                    status=field_data.get("status", "missing"),
                    confidence_score=field_data.get("confidence"),
                    source_file_id=evidence.get("source_document_id"),
                    source_excerpt=evidence.get("source_excerpt"),
                    document_values=doc_vals if doc_vals else None,
                    notes=field_data.get("notes"),
                )
                db.session.add(existing)

        db.session.flush()
