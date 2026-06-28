from fastapi import APIRouter, HTTPException
from app.services.gee_service import initialize_gee, get_lst_tile_url, _expand_bbox
from app.services.geocoding_service import geocode_city
from datetime import datetime

router = APIRouter()

@router.get("/lst")
async def get_lst(city: str, year1: int, year2: int):
    current_year = datetime.now().year

    if year1 < 1984 or year2 < 1984:
        raise HTTPException(status_code=400, detail="No Landsat data available before 1984.")
    if year1 >= current_year or year2 >= current_year:
        raise HTTPException(status_code=400, detail=f"Years must be before {current_year}.")
    if year1 == year2:
        raise HTTPException(status_code=400, detail="Please select two different years.")

    try:
        initialize_gee()
        location = await geocode_city(city)
        bbox = location["bbox"]
        expanded_bbox = _expand_bbox(bbox, factor=0.6)

        result1 = get_lst_tile_url(bbox, year1)
        result2 = get_lst_tile_url(bbox, year2)

        diff = None
        if result1["mean_lst"] and result2["mean_lst"]:
            diff = round(result2["mean_lst"] - result1["mean_lst"], 2)

        return {
            "city": location["name"],
            "lat": location["lat"],
            "lon": location["lon"],
            "bbox": bbox,           # original city bbox (for reference)
            "tile_bbox": expanded_bbox,  # expanded bbox — use this for map fitBounds
            "year1": {
                "year": year1,
                "tile_url": result1["tile_url"],
                "mean_lst": result1["mean_lst"]
            },
            "year2": {
                "year": year2,
                "tile_url": result2["tile_url"],
                "mean_lst": result2["mean_lst"]
            },
            "difference": diff
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))