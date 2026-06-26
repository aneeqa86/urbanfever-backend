from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()


@router.get("")
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
    }
