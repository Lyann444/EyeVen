import unittest

from app.services.ocr_service import _confidence_summary


class OcrConfidenceSummaryTests(unittest.TestCase):
    def test_summarizes_average_minimum_and_low_confidence_blocks(self):
        result = _confidence_summary([
            {"text": "clear text", "confidence": 0.92},
            {"text": "unclear text", "confidence": 0.41},
        ])

        self.assertEqual(result["average_confidence"], 0.665)
        self.assertEqual(result["minimum_confidence"], 0.41)
        self.assertEqual(result["low_confidence_block_count"], 1)
        self.assertEqual(result["quality_status"], "low_confidence")

    def test_empty_ocr_result_is_classified_as_unreadable(self):
        result = _confidence_summary([])

        self.assertIsNone(result["average_confidence"])
        self.assertIsNone(result["minimum_confidence"])
        self.assertEqual(result["quality_status"], "unreadable")


if __name__ == "__main__":
    unittest.main()
