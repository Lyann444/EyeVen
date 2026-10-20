import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://eyeven:eyeven_password@localhost:3306/eyeven",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }

    # Keep the existing GLM config keys because current services use them,
    # but allow the project to use OpenAI directly via the .env variables.
    GLM_API_URL = os.getenv(
        "OPENAI_API_URL",
        os.getenv("GLM_API_URL", "https://api.openai.com/v1/chat/completions"),
    )
    GLM_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("GLM_API_KEY", "")
    GLM_MODEL = os.getenv("OPENAI_MODEL") or os.getenv("GLM_MODEL", "gpt-4o-mini")

    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    MAX_ZIP_FILES = int(os.getenv("MAX_ZIP_FILES", "20"))
    MAX_AGENTIC_ACTIONS = int(os.getenv("MAX_AGENTIC_ACTIONS", "3"))
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")

    PADDLEOCR_LANG = os.getenv("PADDLEOCR_LANG", "en")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
