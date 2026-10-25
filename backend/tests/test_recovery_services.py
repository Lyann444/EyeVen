import unittest
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from app.orchestrators.reprocessing_orchestrator import ReprocessingOrchestrator
from app.orchestrators.workflow_orchestrator import WorkflowOrchestrator
from app.services.case_service import CaseService
from app.services.file_service import FileService
from app.services.form_service import FormService
from app.services.safety_gate import SafetyGate


class RecoveryServiceTests(unittest.TestCase):
    def test_manual_correction_is_marked_unverified_and_reprocesses_verification(self):
        field = SimpleNamespace(
            field_name="contact_email",
            value_text="old@example.com",
            normalized_value_text="old@example.com",
            value_source="supporting_document",
            status="matched_with_evidence",
            confidence_score=0.9,
            source_file_id=4,
            source_excerpt="Original evidence",
            notes=None,
        )
        reprocess_patch = patch(
            "app.services.case_service.ReprocessingOrchestrator.reprocess",
            return_value={"status": "completed"},
        )
        patches = [
            patch(
                "app.services.case_service.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(get_or_404=Mock())),
            ),
            patch(
                "app.services.case_service.EyeVenFormFieldValue",
                new=SimpleNamespace(
                    query=SimpleNamespace(
                        filter_by=Mock(return_value=SimpleNamespace(all=Mock(return_value=[field])))
                    )
                ),
            ),
            patch("app.services.case_service.db.session.commit"),
            reprocess_patch,
        ]
        reprocess = None
        for item in patches:
            started = item.start()
            self.addCleanup(item.stop)
            if item is reprocess_patch:
                reprocess = started

        result = CaseService.correct_fields(31, [{
            "field_name": "contact_email",
            "value_text": "new@example.com",
        }])

        self.assertEqual(field.value_text, "new@example.com")
        self.assertEqual(field.value_source, "unknown")
        self.assertEqual(field.status, "low_confidence")
        self.assertIsNone(field.source_file_id)
        self.assertIsNone(field.source_excerpt)
        self.assertIsNone(field.confidence_score)
        self.assertIn("old@example.com", field.notes)
        self.assertIn("Previous source file ID: 4", field.notes)
        self.assertIn("not independently verified", field.notes)
        self.assertFalse(result["manual_values_are_independently_verified"])
        reprocess.assert_called_once_with(31, "verification", "manual_correction")

    def test_vendor_response_is_not_requested_again_after_correction(self):
        field = SimpleNamespace(
            field_name="contact_email",
            value_text=None,
            normalized_value_text=None,
            value_source="unknown",
            status="missing",
            confidence_score=None,
            source_file_id=None,
            source_excerpt=None,
            notes=None,
        )
        with (
            patch(
                "app.services.case_service.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(get_or_404=Mock())),
            ),
            patch(
                "app.services.case_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(
                    filter_by=Mock(return_value=SimpleNamespace(all=Mock(return_value=[field])))
                )),
            ),
            patch(
                "app.services.case_service.UploadedFile",
                new=SimpleNamespace(query=SimpleNamespace(
                    filter_by=Mock(return_value=SimpleNamespace(first=Mock(return_value=object())))
                )),
            ),
            patch("app.services.case_service.db.session.commit"),
            patch(
                "app.services.case_service.ReprocessingOrchestrator.reprocess",
                return_value={"status": "completed"},
            ),
        ):
            CaseService.correct_fields(31, [{
                "field_name": "contact_email",
                "value_text": "vendor@example.com",
                "source_file_id": 42,
                "confirmed_against_source": True,
                "vendor_response": True,
            }])

        self.assertEqual(field.value_source, "vendor_response")
        self.assertEqual(field.source_file_id, 42)
        self.assertIn("Vendor response received and transcribed by PIC.", field.notes)
        self.assertFalse(SafetyGate.is_follow_up_needed(field, {
            "contact_email": "vendor@example.com",
        }))
        self.assertEqual(field.status, "low_confidence")

    def test_correction_cannot_claim_source_confirmation_without_a_document(self):
        field = SimpleNamespace(field_name="contact_email")
        with (
            patch(
                "app.services.case_service.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(get_or_404=Mock())),
            ),
            patch(
                "app.services.case_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(all=Mock(return_value=[field]))
                ))),
            ),
            patch("app.services.case_service.db.session.commit") as commit,
        ):
            with self.assertRaisesRegex(ValueError, "Select a supporting document"):
                CaseService.correct_fields(31, [{
                    "field_name": "contact_email",
                    "value_text": "new@example.com",
                    "confirmed_against_source": True,
                }])

        commit.assert_not_called()

    def test_manual_correction_rejects_unknown_field_before_persisting(self):
        with (
            patch(
                "app.services.case_service.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(get_or_404=Mock())),
            ),
            patch(
                "app.services.case_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(all=Mock(return_value=[]))
                ))),
            ),
            patch("app.services.case_service.db.session.commit") as commit,
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported case field"):
                CaseService.correct_fields(31, [{
                    "field_name": "unapproved_field",
                    "value_text": "value",
                }])

        commit.assert_not_called()

    def test_follow_up_draft_edit_marks_it_reviewed_without_sending(self):
        draft = SimpleNamespace(
            draft_status="generated",
            recipient_email="old@example.com",
            subject_line="Old subject",
            body_text="Old body",
            id=17,
        )
        with (
            patch(
                "app.services.case_service.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(get_or_404=Mock())),
            ),
            patch(
                "app.services.case_service.EmailDraft",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=draft))
                ))),
            ),
            patch("app.services.case_service.db.session.commit") as commit,
        ):
            result = CaseService.update_follow_up_draft(31, 17, {
                "recipient_email": "new@example.com",
                "subject_line": "Updated subject",
                "body_text": "Updated body",
            })

        self.assertEqual(draft.draft_status, "reviewed")
        self.assertEqual(draft.recipient_email, "new@example.com")
        self.assertEqual(draft.subject_line, "Updated subject")
        self.assertEqual(draft.body_text, "Updated body")
        self.assertEqual(result["draft_status"], "reviewed")
        commit.assert_called_once()

    def test_recovery_upload_rejects_empty_or_unsupported_files(self):
        app = Flask(__name__)
        app.config["MAX_UPLOAD_SIZE_MB"] = 1
        with app.app_context():
            empty_file = FileStorage(
                stream=BytesIO(b""), filename="empty.pdf", content_type="application/pdf"
            )
            with self.assertRaisesRegex(ValueError, "File is empty"):
                FileService._validate_files([empty_file])

            unsupported_file = FileStorage(
                stream=BytesIO(b"content"), filename="script.exe"
            )
            with self.assertRaisesRegex(ValueError, "Unsupported file type"):
                FileService._validate_files([unsupported_file])

    def test_recovery_upload_saves_document_and_waits_for_manual_workflow_run(self):
        case = SimpleNamespace(current_state="pending_follow_up")
        uploaded_file = {
            "id": 12,
            "original_filename": "missing.pdf",
            "file_type": "pdf",
            "source_category": "corrected_upload",
            "upload_status": "stored",
        }
        with (
            patch(
                "app.services.file_service.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(get_or_404=Mock(return_value=case))),
            ),
            patch.object(FileService, "_validate_files"),
            patch.object(FileService, "_get_upload_dir", return_value="uploads\\31"),
            patch("app.services.file_service.os.makedirs"),
            patch.object(
                FileService, "_process_single_file", return_value=uploaded_file
            ) as process_file,
            patch("app.services.file_service.db.session.commit") as commit,
            patch(
                "app.orchestrators.reprocessing_orchestrator.ReprocessingOrchestrator.reprocess"
            ) as reprocess,
        ):
            result = FileService.upload_recovery_files(31, [object()])

        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(case.current_state, "intake_received")
        self.assertIn("Run the workflow", result["message"])
        process_file.assert_called_once()
        commit.assert_called_once()
        reprocess.assert_not_called()

    def test_document_restructuring_preserves_manual_correction_history(self):
        field = SimpleNamespace(
            notes="Original OCR note.\nPIC manual correction; not independently verified. Previous extracted value: None.",
        )
        with (
            patch(
                "app.services.form_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=field))
                ))),
            ),
            patch("app.services.form_service.db.session.flush"),
        ):
            FormService._persist_form_fields(31, {"fields": [{
                "field_name": "contact_email",
                "extracted_value": "new@example.com",
                "normalized_value": "new@example.com",
                "value_source": "supporting_document",
                "status": "filled_from_supporting_docs",
                "confidence": 0.9,
                "notes": "Extracted from the newly uploaded document.",
            }]})

        self.assertIn("Extracted from the newly uploaded document.", field.notes)
        self.assertIn("PIC manual correction; not independently verified.", field.notes)

    def test_supporting_document_field_confidence_is_high_for_demo(self):
        field = SimpleNamespace(notes=None)
        with (
            patch(
                "app.services.form_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=field))
                ))),
            ),
            patch("app.services.form_service.db.session.flush"),
        ):
            FormService._persist_form_fields(31, {"fields": [{
                "field_name": "contact_email",
                "extracted_value": "vendor@example.com",
                "normalized_value": "vendor@example.com",
                "value_source": "supporting_document",
                "status": "filled_from_supporting_docs",
                "confidence": 0.65,
                "notes": "Found in uploaded supporting document.",
            }]})

        self.assertEqual(field.confidence_score, 0.9)
        self.assertEqual(field.status, "filled_from_supporting_docs")

    def test_unknown_source_low_confidence_value_is_grounded_in_supporting_ocr(self):
        field = SimpleNamespace(notes=None)
        with (
            patch(
                "app.services.form_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=field))
                ))),
            ),
            patch("app.services.form_service.db.session.flush"),
        ):
            FormService._persist_form_fields(
                31,
                {"fields": [{
                    "field_name": "latest_annual_revenue_range",
                    "extracted_value": "RM500k - RM5m",
                    "normalized_value": "RM500k - RM5m",
                    "value_source": "unknown",
                    "status": "low_confidence",
                    "confidence": 0.55,
                    "notes": None,
                }]},
                {
                    "71": [{
                        "text": "Latest Annual Revenue Range RM500k - RM5m",
                        "confidence": 0.97,
                    }]
                },
                {"71": "supporting_document"},
            )

        self.assertEqual(field.value_source, "supporting_document")
        self.assertEqual(field.status, "filled_from_supporting_docs")
        self.assertEqual(field.confidence_score, 0.9)
        self.assertEqual(field.source_file_id, 71)
        self.assertIn("RM500k - RM5m", field.source_excerpt)

    def test_uploaded_supporting_document_boosts_populated_demo_field_without_ocr_match(self):
        field = SimpleNamespace(notes=None)
        with (
            patch(
                "app.services.form_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=field))
                ))),
            ),
            patch("app.services.form_service.db.session.flush"),
        ):
            FormService._persist_form_fields(
                31,
                {"fields": [{
                    "field_name": "latest_annual_revenue_range",
                    "extracted_value": "RM500k - RM5m",
                    "normalized_value": "RM500k - RM5m",
                    "value_source": "unknown",
                    "status": "low_confidence",
                    "confidence": None,
                    "evidence_reference": {
                        "source_document_id": None,
                        "source_excerpt": None,
                    },
                    "notes": None,
                }]},
                source_categories_by_file_id={"71": "supporting_document"},
            )

        self.assertEqual(field.value_source, "supporting_document")
        self.assertEqual(field.status, "filled_from_supporting_docs")
        self.assertEqual(field.confidence_score, 0.9)
        self.assertIn("Demo mode", field.notes)

    def test_supporting_document_does_not_raise_conflicting_field_confidence(self):
        field = SimpleNamespace(notes=None)
        with (
            patch(
                "app.services.form_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=field))
                ))),
            ),
            patch("app.services.form_service.db.session.flush"),
        ):
            FormService._persist_form_fields(31, {"fields": [{
                "field_name": "contact_email",
                "extracted_value": "vendor@example.com",
                "normalized_value": "vendor@example.com",
                "value_source": "supporting_document",
                "status": "conflicting",
                "confidence": 0.35,
                "notes": "Another document shows a different value.",
            }]})

        self.assertEqual(field.confidence_score, 0.35)
        self.assertEqual(field.status, "conflicting")

    def test_low_ocr_confidence_marks_supporting_document_field_for_pic_review(self):
        field = SimpleNamespace(notes=None)
        with (
            patch(
                "app.services.form_service.EyeVenFormFieldValue",
                new=SimpleNamespace(query=SimpleNamespace(filter_by=Mock(
                    return_value=SimpleNamespace(first=Mock(return_value=field))
                ))),
            ),
            patch("app.services.form_service.db.session.flush"),
        ):
            FormService._persist_form_fields(
                31,
                {"fields": [{
                    "field_name": "company_registered_number",
                    "extracted_value": "202001012345",
                    "normalized_value": "202001012345",
                    "value_source": "supporting_document",
                    "status": "filled_from_supporting_docs",
                    "confidence": 0.95,
                    "evidence_reference": {
                        "source_document_id": 71,
                        "source_excerpt": "Company registration number 202001012345",
                    },
                    "notes": "Found in uploaded supporting document.",
                }]},
                {
                    "71": [
                        {
                            "text": "Company registration number 202001012345",
                            "confidence": 0.42,
                        }
                    ]
                },
            )

        self.assertEqual(field.status, "low_confidence")
        self.assertEqual(field.confidence_score, 0.42)
        self.assertIn("OCR confidence: 0.42", field.notes)

    def test_matches_ocr_confidence_to_the_cited_text_block(self):
        confidence = FormService._ocr_confidence_for_excerpt(
            "Contact email: vendor@example.com",
            [
                {"text": "Company name: Example Ltd", "confidence": 0.98},
                {"text": "Contact email: vendor@example.com", "confidence": 0.77},
            ],
        )

        self.assertEqual(confidence, 0.77)

    def test_run_stage_uses_recoverable_failure_reason(self):
        with patch.object(
            ReprocessingOrchestrator,
            "reprocess",
            return_value={"status": "failed"},
        ) as reprocess:
            result = ReprocessingOrchestrator.run_stage(31, "verification")

        self.assertEqual(result["status"], "failed")
        reprocess.assert_called_once_with(31, "verification", "recoverable_failure")

    def test_unchanged_successful_input_is_skipped_but_keeps_unresolved_issues_visible(self):
        started_at = Mock()
        previous_run = SimpleNamespace(reason_summary=json.dumps({
            "stage": "verification",
            "input_fingerprint": "same-input",
        }))
        successful_runs = SimpleNamespace(
            order_by=Mock(return_value=SimpleNamespace(all=Mock(return_value=[previous_run])))
        )
        run_model = SimpleNamespace(
            started_at=started_at,
            query=SimpleNamespace(filter_by=Mock(return_value=successful_runs)),
        )
        with (
            patch(
                "app.orchestrators.reprocessing_orchestrator.VendorCase",
                new=SimpleNamespace(query=SimpleNamespace(
                    get_or_404=Mock(return_value=SimpleNamespace(current_state="ready"))
                )),
            ),
            patch(
                "app.orchestrators.reprocessing_orchestrator.ReprocessingRun",
                new=run_model,
            ),
            patch.object(ReprocessingOrchestrator, "_validate_prerequisites"),
            patch.object(
                ReprocessingOrchestrator,
                "_input_fingerprint",
                return_value="same-input",
            ),
            patch.object(
                ReprocessingOrchestrator,
                "_unresolved_issues",
                return_value=["contact_email"],
            ),
            patch.object(WorkflowOrchestrator, "_run_verification") as run_verification,
        ):
            result = ReprocessingOrchestrator.reprocess(
                31, "verification", "manual_correction"
            )

        self.assertEqual(result["status"], "skipped_unchanged")
        self.assertEqual(result["unresolved_issues"], ["contact_email"])
        self.assertTrue(result["requires_further_processing"])
        run_verification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
