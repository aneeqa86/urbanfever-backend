import ee
from app.core.config import settings

def initialize_gee():
    credentials = ee.ServiceAccountCredentials(
        settings.GEE_SERVICE_ACCOUNT,
        settings.GEE_KEY_FILE
    )
    ee.Initialize(credentials)
