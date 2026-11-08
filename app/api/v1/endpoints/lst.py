"""Legacy endpoint kept for backwards compatibility — use /analysis/overview instead."""
from fastapi import APIRouter, Depends

from app.api.v1.endpoints.analysis import Query2, _run, common
from app.services.analysis.overview import overview

router = APIRouter()


@router.get("/lst", deprecated=True)
def get_lst(q: Query2 = Depends(common)):
    r = _run(overview, q.city, q.year1, q.year2, q.start_month, q.end_month)
    y1, y2 = r["years"]
    return {
        "city": r["city"],
        "lat": r["lat"],
        "lon": r["lon"],
        "bbox": r["map_bounds"],
        "tile_bbox": r["map_bounds"],
        "year1": {"year": y1["year"], "tile_url": y1["tile_url"], "mean_lst": y1["urban_mean"]},
        "year2": {"year": y2["year"], "tile_url": y2["tile_url"], "mean_lst": y2["urban_mean"]},
        "difference": r["delta"]["urban_change"],
    }
