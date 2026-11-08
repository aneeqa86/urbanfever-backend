from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    PROJECT_NAME: str = "UrbanFever"
    PROJECT_DESCRIPTION: str = "Is your city getting hotter? Urban heat analysis from 40 years of Landsat."
    VERSION: str = "0.2.0"
    API_V1_PREFIX: str = "/api/v1"

    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000", "https://urbanfever.vercel.app"]
    ALLOWED_ORIGIN_REGEX: str = ""  # e.g. Vercel preview deployments

    # Earth Engine credentials — first match wins:
    #   1. GEE_SERVICE_ACCOUNT_KEY  (full JSON key content, for Render/Railway/etc.)
    #   2. GEE_KEY_FILE             (path to JSON key, local development)
    #   3. Application Default Credentials (Cloud Run running as the service account)
    GEE_SERVICE_ACCOUNT: str = ""
    GEE_SERVICE_ACCOUNT_KEY: str = ""
    GEE_KEY_FILE: str = "service_account.json"
    GEE_PROJECT_ID: str = ""

    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"
    NOMINATIM_USER_AGENT: str = "UrbanFever/0.2 (urban heat analysis tool)"

    CACHE_TTL_SECONDS: int = 6 * 3600

    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
