import logging
import os

logger = logging.getLogger(__name__)

OCR_SUPPORTED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp", "gif"}

_ocr_engine = None


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
            }

        engine = _get_ocr_engine(lang)
        if engine is None:
            return {
                "source_file": file_path,
                "text_blocks": [],
                "full_text": "",
                "page_count": 0,
                "status": "ocr_unavailable",
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
        }
