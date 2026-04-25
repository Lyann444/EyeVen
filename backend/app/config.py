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

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    GLM_API_KEY = os.getenv("GLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    GLM_API_URL = os.getenv("GLM_API_URL", "https://api.openai.com/v1/chat/completions")
    GLM_MODEL = os.getenv("GLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    MAX_ZIP_FILES = int(os.getenv("MAX_ZIP_FILES", "20"))
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")

    PADDLEOCR_LANG = os.getenv("PADDLEOCR_LANG", "en")

    # External verification APIs
    SSM_EINFO_BASE_URL = os.getenv("SSM_EINFO_BASE_URL", "https://www.ssm-einfo.my/api")
    SSM_EINFO_API_KEY = os.getenv("SSM_EINFO_API_KEY", "")

    SST_INQUIRY_BASE_URL = os.getenv("SST_INQUIRY_BASE_URL", "https://sst01.customs.gov.my/api")
    SST_INQUIRY_API_KEY = os.getenv("SST_INQUIRY_API_KEY", "")

    LHDN_TIN_BASE_URL = os.getenv("LHDN_TIN_BASE_URL", "https://mytax.hasil.gov.my/api")
    LHDN_TIN_API_KEY = os.getenv("LHDN_TIN_API_KEY", "")

    EXTERNAL_API_TIMEOUT = int(os.getenv("EXTERNAL_API_TIMEOUT", "15"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
