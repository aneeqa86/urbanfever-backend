from fastapi import APIRouter, HTTPException
from app.services.gee_service import initialize_gee, get_lst_tile_url
from app.services.geocoding_service import geocode_city

router = APIRouter()

@router.get("/lst")
async def get_lst(city: str, year1: int, year2: int):
    try:
        initialize_gee()
        location = await geocode_city(city)
        bbox = location["bbox"]

        result1 = get_lst_tile_url(bbox, year1)
        result2 = get_lst_tile_url(bbox, year2)

        diff = None
        if result1["mean_lst"] and result2["mean_lst"]:
            diff = round(result2["mean_lst"] - result1["mean_lst"], 2)

        return {
            "city": location["name"],
            "lat": location["lat"],
            "lon": location["lon"],
            "bbox": bbox,
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
