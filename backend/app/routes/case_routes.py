from flask import Blueprint, request, jsonify
from app.services.case_service import CaseService

case_bp = Blueprint("case_routes", __name__)


@case_bp.route("", methods=["POST"])
def create_case():
    data = request.get_json()
    result = CaseService.create_case(data)
    return jsonify(result), 201


@case_bp.route("", methods=["GET"])
def list_cases():
    result = CaseService.list_cases()
    return jsonify(result), 200


@case_bp.route("/<int:case_id>", methods=["GET"])
def get_case_detail(case_id):
    result = CaseService.get_case_detail(case_id)
    return jsonify(result), 200


@case_bp.route("/<int:case_id>/review", methods=["POST"])
def mark_case_reviewed(case_id):
    result = CaseService.mark_reviewed(case_id)
    return jsonify(result), 200
