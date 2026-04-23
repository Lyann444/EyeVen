from flask import Blueprint, jsonify
from app.services.case_service import CaseService

workflow_bp = Blueprint("workflow_routes", __name__)


@workflow_bp.route("/<int:case_id>/run", methods=["POST"])
def run_workflow(case_id):
    result = CaseService.run_workflow(case_id)
    return jsonify(result), 200


@workflow_bp.route("/<int:case_id>/rerun", methods=["POST"])
def rerun_stages(case_id):
    result = CaseService.rerun_affected_stages(case_id)
    return jsonify(result), 200


@workflow_bp.route("/<int:case_id>/form-alignment", methods=["GET"])
def get_form_alignment(case_id):
    result = CaseService.get_form_alignment(case_id)
    return jsonify(result), 200


@workflow_bp.route("/<int:case_id>/verification", methods=["GET"])
def get_verification(case_id):
    result = CaseService.get_verification(case_id)
    return jsonify(result), 200


@workflow_bp.route("/<int:case_id>/policy", methods=["GET"])
def get_policy(case_id):
    result = CaseService.get_policy(case_id)
    return jsonify(result), 200


@workflow_bp.route("/<int:case_id>/routing", methods=["GET"])
def get_routing(case_id):
    result = CaseService.get_routing(case_id)
    return jsonify(result), 200


@workflow_bp.route("/<int:case_id>/follow-up", methods=["POST"])
def create_follow_up(case_id):
    result = CaseService.create_follow_up(case_id)
    return jsonify(result), 201
