from flask import Blueprint, jsonify
from app.services.audit_service import AuditService

audit_bp = Blueprint("audit_routes", __name__)


@audit_bp.route("/<int:case_id>", methods=["GET"])
def get_audit_trail(case_id):
    try:
        result = AuditService.get_audit_trail(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@audit_bp.route("/<int:case_id>/reprocessing", methods=["GET"])
def get_reprocessing_history(case_id):
    try:
        result = AuditService.get_reprocessing_history(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404
