from flask import Blueprint, jsonify, request
from app.services.case_service import CaseService

workflow_bp = Blueprint("workflow_routes", __name__)


@workflow_bp.route("/<int:case_id>/run", methods=["POST"])
def run_workflow(case_id):
    try:
        result = CaseService.run_workflow(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@workflow_bp.route("/<int:case_id>/rerun", methods=["POST"])
def rerun_stages(case_id):
    try:
        result = CaseService.rerun_affected_stages(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@workflow_bp.route("/<int:case_id>/form-alignment", methods=["GET"])
def get_form_alignment(case_id):
    try:
        result = CaseService.get_form_alignment(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@workflow_bp.route("/<int:case_id>/verification", methods=["GET"])
def get_verification(case_id):
    try:
        result = CaseService.get_verification(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@workflow_bp.route("/<int:case_id>/policy", methods=["GET"])
def get_policy(case_id):
    try:
        result = CaseService.get_policy(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@workflow_bp.route("/<int:case_id>/routing", methods=["GET"])
def get_routing(case_id):
    try:
        result = CaseService.get_routing(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@workflow_bp.route("/<int:case_id>/follow-up", methods=["POST"])
def create_follow_up(case_id):
    try:
        result = CaseService.create_follow_up(case_id)
        if result.get("status") == "glm_failed":
            return jsonify(result), 502
        if result.get("status") == "no_eligible_follow_up_fields":
            return jsonify(result), 409
        status_code = 200 if result.get("status") == "already_available" else 201
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@workflow_bp.route("/<int:case_id>/reprocess", methods=["POST"])
def reprocess_stage(case_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON request body is required"}), 400
    stage = data.get("stage")
    reason = data.get("reason")
    if not isinstance(stage, str) or not isinstance(reason, str):
        return jsonify({"error": "stage and reason are required strings"}), 400
    try:
        return jsonify(CaseService.reprocess_stage(case_id, stage, reason)), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@workflow_bp.route("/<int:case_id>/follow-up/<int:draft_id>", methods=["PATCH"])
def update_follow_up_draft(case_id, draft_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON request body is required"}), 400
    try:
        return jsonify(
            CaseService.update_follow_up_draft(case_id, draft_id, data)
        ), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return jsonify({"error": str(error)}), 500
