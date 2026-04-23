from flask import Blueprint, request, jsonify
from app.services.file_service import FileService

upload_bp = Blueprint("upload_routes", __name__)


@upload_bp.route("/<int:case_id>", methods=["POST"])
def upload_files(case_id):
    files = request.files.getlist("files")
    email_content = request.form.get("email_content")
    result = FileService.upload_files(case_id, files, email_content)
    return jsonify(result), 201


@upload_bp.route("/<int:case_id>/corrected", methods=["POST"])
def upload_corrected_files(case_id):
    files = request.files.getlist("files")
    result = FileService.upload_corrected_files(case_id, files)
    return jsonify(result), 201
