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

    GLM_API_URL = os.getenv("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    GLM_API_KEY = os.getenv("GLM_API_KEY", "")
    GLM_MODEL = os.getenv("GLM_MODEL", "glm-4")

    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    MAX_ZIP_FILES = int(os.getenv("MAX_ZIP_FILES", "20"))
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
