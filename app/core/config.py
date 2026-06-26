from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "UrbanFever"
    PROJECT_DESCRIPTION: str = "Is your city getting hotter?"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    GEE_SERVICE_ACCOUNT: str = ""
    GEE_KEY_FILE: str = "service_account.json"

    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()
