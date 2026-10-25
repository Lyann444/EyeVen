import logging
import os

logger = logging.getLogger(__name__)

OCR_SUPPORTED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp", "gif"}
LOW_CONFIDENCE_THRESHOLD = 0.6

_ocr_engine = None


def _confidence_summary(text_blocks):
    confidences = [
        float(block["confidence"])
        for block in text_blocks
        if isinstance(block.get("confidence"), (int, float))
    ]
    if not confidences:
        return {
            "average_confidence": None,
            "minimum_confidence": None,
            "low_confidence_block_count": 0,
            "quality_status": "unreadable" if not text_blocks else "unscored",
        }

    return {
        "average_confidence": round(sum(confidences) / len(confidences), 4),
        "minimum_confidence": round(min(confidences), 4),
        "low_confidence_block_count": sum(
            confidence < LOW_CONFIDENCE_THRESHOLD for confidence in confidences
        ),
        "quality_status": (
            "low_confidence"
            if any(confidence < LOW_CONFIDENCE_THRESHOLD for confidence in confidences)
            else "readable"
        ),
    }


def _get_ocr_engine(lang="en"):
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from paddleocr import PaddleOCR
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            logger.info("PaddleOCR engine initialized (lang=%s)", lang)
        except ImportError:
            logger.warning("PaddleOCR not available — OCR calls will return empty results")
            _ocr_engine = False
    return _ocr_engine if _ocr_engine is not False else None


class OcrService:
    LOW_CONFIDENCE_THRESHOLD = LOW_CONFIDENCE_THRESHOLD

    @staticmethod
    def run_ocr(file_path, lang="en"):
        ext = os.path.splitext(file_path)[1].lstrip(".").lower()
        if ext not in OCR_SUPPORTED_EXTENSIONS:
            return {
                "source_file": file_path,
                "text_blocks": [],
                "full_text": "",
                "page_count": 0,
                "status": "skipped_unsupported_format",
                **_confidence_summary([]),
            }

        engine = _get_ocr_engine(lang)
        if engine is None:
            return {
                "source_file": file_path,
                "text_blocks": [],
                "full_text": "",
                "page_count": 0,
                "status": "ocr_unavailable",
                **_confidence_summary([]),
            }

        try:
            if ext == "pdf":
                return OcrService._ocr_pdf(engine, file_path)
            else:
                return OcrService._ocr_image(engine, file_path)
        except Exception as e:
            logger.error("OCR failed for %s: %s", file_path, e)
            return {
                "source_file": file_path,
                "text_blocks": [],
                "full_text": "",
                "page_count": 0,
                "status": f"ocr_error: {e}",
                **_confidence_summary([]),
            }

    @staticmethod
    def _ocr_image(engine, file_path):
        result = engine.ocr(file_path, cls=True)

        text_blocks = []
        if result and result[0]:
            for line in result[0]:
                box = line[0]
                text = line[1][0]
                confidence = line[1][1]
                text_blocks.append({
                    "text": text,
                    "confidence": round(confidence, 4),
                    "page": 1,
                })

        full_text = "\n".join(b["text"] for b in text_blocks)

        return {
            "source_file": file_path,
            "text_blocks": text_blocks,
            "full_text": full_text,
            "page_count": 1,
            "status": "completed",
            **_confidence_summary(text_blocks),
        }

    @staticmethod
    def _ocr_pdf(engine, file_path):
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(file_path, dpi=300)
        except ImportError:
            logger.warning("pdf2image not available, attempting direct OCR on PDF")
            return OcrService._ocr_image(engine, file_path)

        all_blocks = []
        for i, image in enumerate(images, start=1):
            temp_path = f"{file_path}_page_{i}.png"
            try:
                image.save(temp_path, "PNG")
                page_result = engine.ocr(temp_path, cls=True)
                if page_result and page_result[0]:
                    for line in page_result[0]:
                        all_blocks.append({
                            "text": line[1][0],
                            "confidence": round(line[1][1], 4),
                            "page": i,
                        })
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        full_text = "\n".join(b["text"] for b in all_blocks)

        return {
            "source_file": file_path,
            "text_blocks": all_blocks,
            "full_text": full_text,
            "page_count": len(images),
            "status": "completed",
            **_confidence_summary(all_blocks),
        }
