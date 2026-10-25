import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.case_service import CaseService


class CaseServiceFollowUpTests(unittest.TestCase):
    def _patch_queries(self, fields, existing_draft=None):
        case = SimpleNamespace(current_state="manual_review_required")
        patches = [
            patch(
                "app.services.case_service.VendorCase",
                new=SimpleNamespace(
                    query=SimpleNamespace(
                        get_or_404=Mock(return_value=case)
                    )
                ),
            ),
            patch(
                "app.services.case_service.EyeVenFormFieldValue",
                new=SimpleNamespace(
                    query=SimpleNamespace(
                        filter_by=Mock(
                            return_value=SimpleNamespace(
                                all=Mock(return_value=fields)
                            )
                        )
                    )
                ),
            ),
            patch(
                "app.services.case_service.EmailDraft",
                new=SimpleNamespace(
                    query=SimpleNamespace(
                        filter_by=Mock(
                            return_value=SimpleNamespace(
                                first=Mock(return_value=existing_draft)
                            )
                        )
                    )
                ),
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return case

    def test_does_not_generate_draft_when_no_follow_up_fields_are_unresolved(self):
        fields = [
            SimpleNamespace(
                field_name="business_type_other",
                status="missing",
                value_text=None,
                normalized_value_text=None,
            )
        ]
        self._patch_queries(fields)

        with patch(
            "app.services.case_service.WorkflowOrchestrator._run_follow_up"
        ) as generate:
            result = CaseService.create_follow_up(12)

        self.assertEqual(result["status"], "no_eligible_follow_up_fields")
        generate.assert_not_called()

    def test_reuses_existing_follow_up_draft(self):
        fields = [
            SimpleNamespace(
                field_name="contact_email",
                status="missing",
                value_text=None,
                normalized_value_text=None,
            )
        ]
        self._patch_queries(fields, existing_draft=SimpleNamespace(id=44))

        with patch(
            "app.services.case_service.WorkflowOrchestrator._run_follow_up"
        ) as generate:
            result = CaseService.create_follow_up(12)

        self.assertEqual(
            result,
            {"status": "already_available", "email_draft_id": 44},
        )
        generate.assert_not_called()

    def test_generates_draft_and_moves_case_to_pending_follow_up(self):
        fields = [
            SimpleNamespace(
                field_name="contact_email",
                status="missing",
                value_text=None,
                normalized_value_text=None,
            )
        ]
        case = self._patch_queries(fields)
        with (
            patch(
                "app.services.case_service.WorkflowOrchestrator._run_follow_up",
                return_value={"status": "completed", "email_draft_id": 44},
            ),
            patch("app.services.case_service.db.session.commit") as commit,
        ):
            result = CaseService.create_follow_up(12)

        self.assertEqual(result["email_draft_id"], 44)
        self.assertEqual(case.current_state, "pending_follow_up")
        commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
