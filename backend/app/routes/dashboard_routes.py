from flask import Blueprint, jsonify
from app.services.dashboard_service import DashboardService

dashboard_bp = Blueprint("dashboard_routes", __name__)


@dashboard_bp.route("/summary", methods=["GET"])
def get_dashboard_summary():
    try:
        result = DashboardService.get_summary()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dashboard_bp.route("/status-distribution", methods=["GET"])
def get_status_distribution():
    try:
        result = DashboardService.get_status_distribution()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dashboard_bp.route("/risk-distribution", methods=["GET"])
def get_risk_distribution():
    try:
        result = DashboardService.get_risk_distribution()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
