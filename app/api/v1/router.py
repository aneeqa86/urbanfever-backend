from fastapi import APIRouter
from app.api.v1.endpoints import analysis, lst

api_router = APIRouter()
api_router.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])
api_router.include_router(lst.router, prefix="/lst", tags=["LST (legacy)"])
