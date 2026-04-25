from sqlalchemy.exc import IntegrityError
from flask import Blueprint, request, jsonify
from app.services.case_service import CaseService

case_bp = Blueprint("case_routes", __name__)


@case_bp.route("", methods=["POST"])
def create_case():
    data = request.get_json(silent=True)
    if not data or "case_reference" not in data:
        return jsonify({"error": "case_reference is required"}), 400
    try:
        result = CaseService.create_case(data)
        return jsonify(result), 201
    except IntegrityError:
        return jsonify({"error": "Case reference already exists"}), 409
    except Exception:
        return jsonify({"error": "Failed to create case"}), 500


@case_bp.route("", methods=["GET"])
def list_cases():
    try:
        result = CaseService.list_cases()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@case_bp.route("/<int:case_id>", methods=["GET"])
def get_case_detail(case_id):
    try:
        result = CaseService.get_case_detail(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@case_bp.route("/<int:case_id>/review", methods=["POST"])
def mark_case_reviewed(case_id):
    try:
        result = CaseService.mark_reviewed(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@staticmethod
def get_email_drafts(case_id):
    drafts = EmailDraft.query.filter_by(
        vendor_case_id=case_id
    ).order_by(EmailDraft.created_at.desc()).all()
    
    return [
        {
            "id": d.id,
            "recipient_email": d.recipient_email,
            "subject_line": d.subject_line,
            "body_text": d.body_text,
            "created_at": d.created_at.isoformat() if d.created_at else None
        }
        for d in drafts
    ]

@case_bp.route("/<int:case_id>/follow-up", methods=["POST"])
def generate_follow_up(case_id):
    try:
        # This triggers the AI to write the email and save it to the DB
        result = CaseService.create_follow_up(case_id)
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@case_bp.route("/<int:case_id>/email-drafts", methods=["GET"])
def get_email_drafts(case_id):
    try:
        # This fetches the saved emails to show on the UI
        result = CaseService.get_email_drafts(case_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500