import logging
import os
import zipfile

from flask import current_app

logger = logging.getLogger(__name__)

DANGEROUS_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".sh", ".dll", ".so", ".scr", ".js", ".vbs"}


class ZipService:
    @staticmethod
    def extract_zip(zip_path, target_dir, max_files=20):
        if not zipfile.is_zipfile(zip_path):
            return {"error": "Not a valid ZIP file", "extracted_files": []}

        os.makedirs(target_dir, exist_ok=True)

        extracted_files = []
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = zf.infolist()

                if len(members) > max_files:
                    logger.warning(
                        "ZIP contains %d files, exceeding limit of %d. Extracting first %d only.",
                        len(members), max_files, max_files,
                    )
                    members = members[:max_files]

                for member in members:
                    if ZipService._is_path_traversal(member.filename):
                        logger.warning("Skipping path-traversal entry: %s", member.filename)
                        continue

                    if ZipService._is_dangerous_extension(member.filename):
                        logger.warning("Skipping dangerous extension: %s", member.filename)
                        continue

                    extracted_path = zf.extract(member, target_dir)

                    if os.path.isfile(extracted_path):
                        extracted_files.append({
                            "filename": os.path.basename(member.filename),
                            "extracted_path": extracted_path,
                            "file_size": os.path.getsize(extracted_path),
                        })

        except zipfile.BadZipFile:
            logger.error("Bad ZIP file: %s", zip_path)
            return {"error": "Corrupt ZIP file", "extracted_files": []}
        except Exception as e:
            logger.error("ZIP extraction error: %s", e)
            return {"error": str(e), "extracted_files": extracted_files}

        return {"extracted_files": extracted_files, "count": len(extracted_files)}

    @staticmethod
    def _is_path_traversal(filename):
        normalized = os.path.normpath(filename)
        return normalized.startswith("..") or os.path.isabs(normalized)

    @staticmethod
    def _is_dangerous_extension(filename):
        ext = os.path.splitext(filename)[1].lower()
        return ext in DANGEROUS_EXTENSIONS
