from collections import Counter

from app.extensions import db
from app.models import VendorCase


class DashboardService:
    @staticmethod
    def get_summary():
        total = VendorCase.query.count()
        pending_follow_up = VendorCase.query.filter_by(current_state="pending_follow_up").count()
        flagged = VendorCase.query.filter_by(current_state="flagged_high_risk").count()
        completed = VendorCase.query.filter_by(current_state="completed").count()
        rejected = VendorCase.query.filter_by(current_state="rejected").count()

        return {
            "total_cases": total,
            "pending_follow_up": pending_follow_up,
            "flagged_high_risk": flagged,
            "completed": completed,
            "rejected": rejected,
        }

    @staticmethod
    def get_status_distribution():
        cases = VendorCase.query.all()
        state_counts = Counter(c.current_state for c in cases)
        return {"distribution": dict(state_counts)}

    @staticmethod
    def get_risk_distribution():
        cases = VendorCase.query.filter(VendorCase.risk_band.isnot(None)).all()
        risk_counts = Counter(c.risk_band for c in cases)
        return {"distribution": dict(risk_counts)}
