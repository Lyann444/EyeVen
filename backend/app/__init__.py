from flask import Flask
from app.config import config_by_name
from app.extensions import db, migrate, cors


def create_app(config_name="development"):
    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app)

    from app.models import (  # noqa: F401 — register models with SQLAlchemy
        VendorCase,
        UploadedFile,
        EyeVenFormFieldValue,
        VerificationRun,
        PolicyEvaluationRun,
        RoutingDecision,
        EmailDraft,
        ReprocessingRun,
    )

    from app.routes.case_routes import case_bp
    from app.routes.upload_routes import upload_bp
    from app.routes.workflow_routes import workflow_bp
    from app.routes.dashboard_routes import dashboard_bp
    from app.routes.audit_routes import audit_bp

    app.register_blueprint(case_bp, url_prefix="/api/cases")
    app.register_blueprint(upload_bp, url_prefix="/api/uploads")
    app.register_blueprint(workflow_bp, url_prefix="/api/workflow")
    app.register_blueprint(dashboard_bp, url_prefix="/api/dashboard")
    app.register_blueprint(audit_bp, url_prefix="/api/audit")

    return app
