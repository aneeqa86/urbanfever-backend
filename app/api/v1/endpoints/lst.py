from fastapi import APIRouter, HTTPException
from app.services.gee_service import initialize_gee
import ee

router = APIRouter()

@router.get("/lst")
async def get_lst(city: str, year1: int, year2: int, bbox: str):
    try:
        initialize_gee()
        return {"status": "ok", "city": city, "year1": year1, "year2": year2}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
