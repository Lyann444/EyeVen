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
        except TypeError:
            # PaddleOCR 2.x may not accept all kwargs
            from paddleocr import PaddleOCR
            _ocr_engine = PaddleOCR(lang=lang)
            logger.info("PaddleOCR engine initialized with minimal args (lang=%s)", lang)
        except ImportError:
            logger.warning("PaddleOCR not available — OCR calls will return empty results")
            _ocr_engine = False
    return _ocr_engine if _ocr_engine is not False else None


def _pdf_to_images_pypdfium2(file_path):
    import pypdfium2
    images = []
    pdf = pypdfium2.PdfDocument(file_path)
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=300 / 72)
        pil_image = bitmap.to_pil()
        images.append(pil_image)
    pdf.close()
    return images


def _pdf_to_images_pdf2image(file_path):
    from pdf2image import convert_from_path
    return convert_from_path(file_path, dpi=300)


def _parse_ocr_result(result):
    """Parse PaddleOCR result into text_blocks list.

    Handles:
    - Legacy format (v2.x/v3.x <=3.4): result[0] = [[box, (text, confidence)], ...]
    - New format (v3.5+): OCRResult with rec_texts/rec_scores keys
    """
    text_blocks = []

    if not result:
        return text_blocks

    for r in result:
        # New PaddleOCR v3.5+ OCRResult dict-like format
        if hasattr(r, 'get') and 'rec_texts' in r:
            texts = r.get('rec_texts', [])
            scores = r.get('rec_scores', [])
            page_idx = r.get('page_index', 1)
            for text, score in zip(texts, scores if scores else [0.0] * len(texts)):
                text_blocks.append({
                    "text": text,
                    "confidence": round(float(score), 4),
                    "page": page_idx + 1 if isinstance(page_idx, int) else 1,
                })
        # Legacy format: list of [box, (text, confidence)]
        elif isinstance(r, list):
            for line in r:
                if isinstance(line, (list, tuple)) and len(line) >= 2:
                    text_info = line[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        text_blocks.append({
                            "text": text_info[0],
                            "confidence": round(float(text_info[1]), 4),
                            "page": 1,
                        })

    return text_blocks


def _run_ocr_on_image(engine, image_path):
    """Run OCR on a single image using whatever API the engine supports."""
    # Try legacy ocr() first (PaddleOCR 2.x - 3.4)
    if hasattr(engine, 'ocr'):
        result = engine.ocr(image_path, cls=True)
        return _parse_ocr_result(result)
    # Try new predict() API (PaddleOCR 3.5+)
    if hasattr(engine, 'predict'):
        result = engine.predict(image_path)
        return _parse_ocr_result(result)
    raise RuntimeError("PaddleOCR engine has neither ocr() nor predict() method")


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
        text_blocks = _run_ocr_on_image(engine, file_path)
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
        # Convert PDF pages to images first, then OCR each page
        images = None
        try:
            images = _pdf_to_images_pypdfium2(file_path)
            logger.info("Converted PDF via pypdfium2: %d pages", len(images))
        except ImportError:
            logger.debug("pypdfium2 not available")
        except Exception as e:
            logger.warning("pypdfium2 conversion failed: %s", e)

        if images is None:
            try:
                images = _pdf_to_images_pdf2image(file_path)
                logger.info("Converted PDF via pdf2image: %d pages", len(images))
            except ImportError:
                logger.debug("pdf2image not available")
            except Exception as e:
                logger.warning("pdf2image conversion failed: %s", e)

        if images is None:
            logger.warning("No PDF renderer available, attempting direct OCR on PDF")
            return OcrService._ocr_image(engine, file_path)

        all_blocks = []
        for i, image in enumerate(images, start=1):
            temp_path = f"{file_path}_page_{i}.png"
            try:
                image.save(temp_path, "PNG")
                page_blocks = _run_ocr_on_image(engine, temp_path)
                for b in page_blocks:
                    b["page"] = i
                all_blocks.extend(page_blocks)
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
