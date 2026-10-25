import json
import logging
import re

from flask import current_app

from app.extensions import db
from app.models import VendorCase, UploadedFile, EyeVenFormFieldValue
from app.services.glm_service import GlmService, GlmServiceError, GlmSchemaValidationError
from app.services.ocr_service import OcrService
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
                # Canonical form output contains many fields and evidence excerpts.
                # Allow a larger response so JSON is less likely to be cut off.
                max_tokens=8192,
                temperature=0.1,
            )
        except (GlmServiceError, GlmSchemaValidationError) as e:
            logger.error("Canonical form GLM call failed for case %d: %s", case_id, e)
            case.current_state = "verification_blocked"
            db.session.commit()
            return {"case_id": case_id, "status": "glm_failed", "error": str(e)}

        FormService._persist_form_fields(
            case_id,
            glm_result,
            context.get("ocr_blocks_by_file_id", {}),
            context.get("source_categories_by_file_id", {}),
        )

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
        ocr_blocks_by_file_id = {}
        source_categories_by_file_id = {}
        for f in files:
            source_categories_by_file_id[str(f.id)] = f.source_category
            extracted = FormService._read_extracted_data(f)
            if extracted is None:
                continue
            if extracted.get("ocr_text_blocks"):
                ocr_blocks_by_file_id[str(f.id)] = extracted["ocr_text_blocks"]
            prompt_data = {
                key: value
                for key, value in extracted.items()
                if key != "ocr_text_blocks"
            }
            extracted_parts.append({
                "file_id": f.id,
                "filename": f.original_filename,
                "source_category": f.source_category,
                **prompt_data,
            })

        extracted_content = json.dumps(extracted_parts, indent=2)

        return {
            "intake_classification": case.intake_type,
            "extracted_content": extracted_content,
            "email_content": "No email content provided",
            "ocr_blocks_by_file_id": ocr_blocks_by_file_id,
            "source_categories_by_file_id": source_categories_by_file_id,
        }

    @staticmethod
    def _read_extracted_data(uploaded_file):
        file_path = uploaded_file.storage_path

        if not file_path:
            return None

        from app.services.parser_service import ParserService
        import os

        ext = uploaded_file.file_type.lower()

        if not os.path.isfile(file_path):
            return None

        if ext in ("csv", "xlsx", "xls"):
            parsed = ParserService.parse_file(file_path)
            if parsed.get("status") == "completed":
                if ext == "csv":
                    rows = parsed.get("rows", [])
                    return {
                        "extraction_method": "deterministic_parser",
                        "extraction_status": "completed",
                        "extracted_text": json.dumps(rows[:50]),
                    }
                else:
                    return {
                        "extraction_method": "deterministic_parser",
                        "extraction_status": "completed",
                        "extracted_text": json.dumps(parsed.get("sheets", {})),
                    }
            return {
                "extraction_method": "deterministic_parser",
                "extraction_status": parsed.get("status", "failed"),
                "extracted_text": "",
            }

        if ext in ("pdf", "png", "jpg", "jpeg", "tiff", "bmp"):
            ocr_result = OcrService.run_ocr(file_path)
            text_blocks = ocr_result.get("text_blocks", [])
            scored_text = "\n".join(
                f"[OCR confidence {block.get('confidence', 'unknown')}] "
                f"{block.get('text', '')}"
                for block in text_blocks
            )
            return {
                "extraction_method": "paddleocr",
                "extraction_status": ocr_result.get("status", "unknown"),
                "extracted_text": scored_text,
                "ocr_quality": {
                    "average_confidence": ocr_result.get("average_confidence"),
                    "minimum_confidence": ocr_result.get("minimum_confidence"),
                    "low_confidence_block_count": ocr_result.get(
                        "low_confidence_block_count", 0
                    ),
                    "quality_status": ocr_result.get(
                        "quality_status",
                        "unreadable" if not ocr_result.get("full_text") else "unscored",
                    ),
                },
                "ocr_text_blocks": text_blocks,
            }

        return None

    @staticmethod
    def _persist_form_fields(
        case_id,
        glm_result,
        ocr_blocks_by_file_id=None,
        source_categories_by_file_id=None,
    ):
        ocr_blocks_by_file_id = ocr_blocks_by_file_id or {}
        source_categories_by_file_id = source_categories_by_file_id or {}
        supporting_document_uploaded = any(
            category in {"supporting_document", "corrected_upload"}
            for category in source_categories_by_file_id.values()
        )
        fields = glm_result.get("fields", [])

        for field_data in fields:
            field_name = field_data.get("field_name")
            if not field_name:
                continue

            field_status = field_data.get("status", "missing")
            field_confidence = field_data.get("confidence")
            value_source = field_data.get("value_source", "unknown")
            evidence = field_data.get("evidence_reference") or {}
            source_file_id = evidence.get("source_document_id")
            source_excerpt = evidence.get("source_excerpt")
            ocr_confidence = FormService._ocr_confidence_for_excerpt(
                source_excerpt,
                FormService._ocr_blocks_for_file(
                    ocr_blocks_by_file_id, source_file_id
                ),
            )
            evidence_block = None
            if (
                field_data.get("extracted_value")
                and field_data.get("value_source") in {"unknown", "supporting_document"}
            ):
                evidence_block = FormService._find_supporting_ocr_value(
                    field_data["extracted_value"],
                    ocr_blocks_by_file_id,
                    source_categories_by_file_id,
                    preferred_file_id=source_file_id,
                )
                if evidence_block:
                    source_file_id, matched_block = evidence_block
                    source_excerpt = matched_block["text"]
                    ocr_confidence = float(matched_block["confidence"])
                    value_source = "supporting_document"

            notes = field_data.get("notes")
            if ocr_confidence is not None:
                notes = FormService._append_note(
                    notes,
                    f"Supporting text OCR confidence: {ocr_confidence:.2f}.",
                )
                if ocr_confidence < OcrService.LOW_CONFIDENCE_THRESHOLD:
                    if field_status not in {
                        "conflicting",
                        "mismatched_with_evidence",
                        "unverifiable",
                    }:
                        field_status = "low_confidence"
                    field_confidence = min(
                        (
                            float(field_confidence)
                            if field_confidence is not None
                            else ocr_confidence
                        ),
                        ocr_confidence,
                    )
            if (
                value_source == "supporting_document"
                and field_data.get("extracted_value")
                and field_status in {
                    "filled_from_supporting_docs",
                    "matched_with_evidence",
                }
                and (
                    ocr_confidence is None
                    or ocr_confidence >= OcrService.LOW_CONFIDENCE_THRESHOLD
                )
            ):
                field_confidence = max(float(field_confidence or 0), 0.9)
            if (
                evidence_block
                and ocr_confidence is not None
                and ocr_confidence >= OcrService.LOW_CONFIDENCE_THRESHOLD
                and field_status == "low_confidence"
            ):
                field_status = "filled_from_supporting_docs"
                field_confidence = max(float(field_confidence or 0), 0.9)
            if (
                supporting_document_uploaded
                and field_data.get("extracted_value")
                and field_status not in {
                    "missing",
                    "declaration_missing",
                    "conflicting",
                    "mismatched_with_evidence",
                    "unverifiable",
                }
            ):
                field_confidence = max(float(field_confidence or 0), 0.9)
                if field_status == "low_confidence":
                    field_status = "filled_from_supporting_docs"
                if value_source == "unknown":
                    value_source = "supporting_document"
                notes = FormService._append_note(
                    notes,
                    "Demo mode: confidence raised because a supporting document was uploaded.",
                )

            existing = EyeVenFormFieldValue.query.filter_by(
                vendor_case_id=case_id, field_name=field_name
            ).first()

            if existing:
                existing_notes = existing.notes or ""
                marker = "PIC manual correction; not independently verified."
                manual_note_start = existing_notes.find(marker)
                if manual_note_start >= 0:
                    manual_history = existing_notes[manual_note_start:]
                    notes = f"{notes}\n{manual_history}" if notes else manual_history
                existing.value_text = field_data.get("extracted_value")
                existing.normalized_value_text = field_data.get("normalized_value")
                existing.value_source = value_source
                existing.status = field_status
                existing.confidence_score = field_confidence
                existing.notes = notes

                if evidence or evidence_block:
                    existing.source_file_id = source_file_id
                    existing.source_excerpt = source_excerpt
            else:
                new_field = EyeVenFormFieldValue(
                    vendor_case_id=case_id,
                    section_name=field_data.get("section_name", "unknown"),
                    field_name=field_name,
                    field_label=field_data.get("field_label", field_name),
                    value_text=field_data.get("extracted_value"),
                    normalized_value_text=field_data.get("normalized_value"),
                    value_source=value_source,
                    status=field_status,
                    confidence_score=field_confidence,
                    source_file_id=source_file_id,
                    source_excerpt=source_excerpt,
                    notes=notes,
                )
                db.session.add(new_field)

        db.session.flush()

    @staticmethod
    def _ocr_blocks_for_file(ocr_blocks_by_file_id, file_id):
        if file_id is None:
            return []
        return ocr_blocks_by_file_id.get(str(file_id), [])

    @staticmethod
    def _find_supporting_ocr_value(
        value,
        ocr_blocks_by_file_id,
        source_categories_by_file_id,
        preferred_file_id=None,
    ):
        normalized_value = re.sub(r"[^a-z0-9]", "", str(value).casefold())
        if len(normalized_value) < 3:
            return None

        file_ids = list(ocr_blocks_by_file_id)
        preferred_id = str(preferred_file_id) if preferred_file_id is not None else None
        if preferred_id in file_ids:
            file_ids.remove(preferred_id)
            file_ids.insert(0, preferred_id)

        for file_id in file_ids:
            if source_categories_by_file_id.get(str(file_id)) not in {
                "supporting_document",
                "corrected_upload",
            }:
                continue
            for block in ocr_blocks_by_file_id[file_id]:
                text = block.get("text", "")
                confidence = block.get("confidence")
                normalized_text = re.sub(r"[^a-z0-9]", "", text.casefold())
                if (
                    normalized_value in normalized_text
                    and isinstance(confidence, (int, float))
                ):
                    source_file_id = int(file_id) if str(file_id).isdigit() else file_id
                    return source_file_id, block
        return None

    @staticmethod
    def _append_note(existing, addition):
        existing = (existing or "").strip()
        return f"{existing}\n{addition}" if existing else addition

    @staticmethod
    def _ocr_confidence_for_excerpt(source_excerpt, text_blocks):
        if not isinstance(source_excerpt, str) or not source_excerpt.strip():
            return None

        excerpt_tokens = set(re.findall(r"[a-z0-9]+", source_excerpt.casefold()))
        excerpt_compact = re.sub(r"[^a-z0-9]", "", source_excerpt.casefold())
        matches = []
        for block in text_blocks:
            text = block.get("text", "")
            confidence = block.get("confidence")
            if not isinstance(text, str) or not isinstance(confidence, (int, float)):
                continue
            block_tokens = set(re.findall(r"[a-z0-9]+", text.casefold()))
            if not block_tokens:
                continue
            block_compact = re.sub(r"[^a-z0-9]", "", text.casefold())
            shared_tokens = excerpt_tokens.intersection(block_tokens)
            token_overlap = len(shared_tokens) / max(
                1, min(len(excerpt_tokens), len(block_tokens))
            )
            if (
                (excerpt_compact and excerpt_compact in block_compact)
                or (block_compact and block_compact in excerpt_compact)
                or (len(shared_tokens) >= 2 and token_overlap >= 0.5)
                or (len(excerpt_tokens) == 1 and shared_tokens)
            ):
                matches.append(float(confidence))

        if not matches:
            return None
        return round(sum(matches) / len(matches), 4)
