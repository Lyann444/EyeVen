from flask import Blueprint, request, jsonify
from app.services.file_service import FileService

upload_bp = Blueprint("upload_routes", __name__)


@upload_bp.route("/<int:case_id>", methods=["POST"])
def upload_files(case_id):
    files = request.files.getlist("files")
    email_content = request.form.get("email_content")
    if not files:
        return jsonify({"error": "No files provided"}), 400
    try:
        result = FileService.upload_files(case_id, files, email_content)
        if isinstance(result, tuple):
            return jsonify(result[0]), result[1]
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@upload_bp.route("/<int:case_id>/corrected", methods=["POST"])
def upload_corrected_files(case_id):
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400
    try:
        result = FileService.upload_corrected_files(case_id, files)
        if isinstance(result, tuple):
            return jsonify(result[0]), result[1]
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@upload_bp.route("/<int:case_id>/recovery", methods=["POST"])
def upload_recovery_files(case_id):
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400
    try:
        result = FileService.upload_recovery_files(case_id, files)
        status_code = 200 if result["status"] == "duplicate_upload" else 201
        return jsonify(result), status_code
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return jsonify({"error": str(error)}), 500
