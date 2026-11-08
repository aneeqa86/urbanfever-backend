"""
Urban heat analysis endpoints.

All handlers are plain `def` so FastAPI runs the blocking Earth Engine calls in its thread pool
instead of blocking the event loop.
"""
from typing import Optional

import ee
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.analysis import cooling as cooling_svc
from app.services.analysis import drivers as drivers_svc
from app.services.analysis import hotspots as hotspots_svc
from app.services.analysis import overview as overview_svc
from app.services.analysis import tools as tools_svc
from app.services.analysis import trend as trend_svc
from app.services.analysis import zones as zones_svc
from app.services.gee_service import initialize_gee, last_complete_year

router = APIRouter()


class Query2(BaseModel):
    city: str
    year1: int
    year2: int
    start_month: Optional[int] = None
    end_month: Optional[int] = None


def _check_year(y: int, name: str):
    if y < 1984:
        raise HTTPException(400, f"{name}: no Landsat thermal data before 1984.")
    if y > last_complete_year():
        raise HTTPException(400, f"{name}: must be {last_complete_year()} or earlier (a complete season is needed).")


def _check_months(sm, em):
    if (sm is None) != (em is None):
        raise HTTPException(400, "Provide both start_month and end_month, or neither (auto season).")
    for m in (sm, em):
        if m is not None and not 1 <= m <= 12:
            raise HTTPException(400, "Months must be 1–12.")


def common(
    city: str = Query(..., min_length=2, description="City name, e.g. 'Karachi'"),
    year1: int = Query(..., description="Baseline year"),
    year2: int = Query(..., description="Comparison year"),
    start_month: Optional[int] = Query(None, ge=1, le=12, description="Season start month (omit for auto)"),
    end_month: Optional[int] = Query(None, ge=1, le=12, description="Season end month (omit for auto)"),
) -> Query2:
    _check_year(year1, "year1")
    _check_year(year2, "year2")
    if year1 == year2:
        raise HTTPException(400, "Please select two different years.")
    _check_months(start_month, end_month)
    return Query2(city=city.strip().lower(), year1=year1, year2=year2, start_month=start_month, end_month=end_month)


def _run(fn, *args):
    try:
        initialize_gee()
        return fn(*args)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Geocoding service error: {e}")
    except ee.EEException as e:
        msg = str(e)
        code = 429 if "Too many" in msg else 504 if "timed out" in msg.lower() else 502
        raise HTTPException(code, f"Earth Engine: {msg}")


@router.get("/overview", summary="LST per year, SUHI, ΔLST, histograms, data quality")
def get_overview(q: Query2 = Depends(common)):
    return _run(overview_svc.overview, q.city, q.year1, q.year2, q.start_month, q.end_month)


@router.get("/trend", summary="Annual series, Sen's slope + Mann-Kendall, ERA5-Land validation")
def get_trend(
    city: str = Query(..., min_length=2),
    start_month: Optional[int] = Query(None, ge=1, le=12),
    end_month: Optional[int] = Query(None, ge=1, le=12),
    start: Optional[int] = Query(None, description="First year (default 1985)"),
    end: Optional[int] = Query(None, description="Last year (default last complete year)"),
):
    _check_months(start_month, end_month)
    if start is not None:
        _check_year(start, "start")
    if end is not None:
        _check_year(end, "end")
    if start and end and end - start < 7:
        raise HTTPException(400, "Trend analysis needs a window of at least 8 years.")
    return _run(trend_svc.trend, city.strip().lower(), start_month, end_month, start, end)


@router.get("/hotspots", summary="Getis-Ord Gi* hot/cold spots of LST (year 2)")
def get_hotspots(q: Query2 = Depends(common)):
    return _run(hotspots_svc.hotspots, q.city, q.year2, q.start_month, q.end_month)


@router.get("/drivers", summary="NDVI/NDBI/MNDWI vs LST, LST by land-cover class (year 2)")
def get_drivers(q: Query2 = Depends(common)):
    return _run(drivers_svc.drivers, q.city, q.year2, q.start_month, q.end_month)


@router.get("/landchange", summary="Built-up / vegetation change categories vs ΔLST")
def get_landchange(q: Query2 = Depends(common)):
    return _run(drivers_svc.landchange, q.city, q.year1, q.year2, q.start_month, q.end_month)


@router.get("/zones", summary="Hexagon zonal stats, Heat Vulnerability Index, GWR")
def get_zones(q: Query2 = Depends(common)):
    return _run(zones_svc.zones, q.city, q.year1, q.year2, q.start_month, q.end_month)


@router.get("/cooling", summary="Distance-decay cooling effect of green space and water (year 2)")
def get_cooling(q: Query2 = Depends(common)):
    return _run(cooling_svc.cooling, q.city, q.year2, q.start_month, q.end_month)


class TransectBody(BaseModel):
    coords: list[list[float]] = Field(..., min_length=2, max_length=50, description="[[lon, lat], ...]")
    year1: int
    year2: int
    start_month: Optional[int] = None
    end_month: Optional[int] = None


@router.post("/transect", summary="LST / NDVI profile along a user-drawn line")
def post_transect(body: TransectBody):
    _check_year(body.year1, "year1")
    _check_year(body.year2, "year2")
    _check_months(body.start_month, body.end_month)
    coords = [[float(c[0]), float(c[1])] for c in body.coords if len(c) >= 2]
    return _run(tools_svc.transect, coords, body.year1, body.year2, body.start_month, body.end_month)


@router.get("/pixel", summary="All layer values + annual LST series at a point")
def get_pixel(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    year1: int = Query(...),
    year2: int = Query(...),
    start_month: Optional[int] = Query(None, ge=1, le=12),
    end_month: Optional[int] = Query(None, ge=1, le=12),
):
    _check_year(year1, "year1")
    _check_year(year2, "year2")
    _check_months(start_month, end_month)
    return _run(tools_svc.pixel, round(lat, 5), round(lon, 5), year1, year2, start_month, end_month)


@router.get("/export", summary="GeoTIFF download URL for a layer")
def get_export(q: Query2 = Depends(common), layer: str = Query(..., description=", ".join(tools_svc.EXPORT_LAYERS))):
    return _run(tools_svc.export, q.city, q.year1, q.year2, q.start_month, q.end_month, layer)


@router.get("/export/layers", summary="Available export layers")
def get_export_layers():
    return [{"layer": k, "description": v} for k, v in tools_svc.EXPORT_LAYERS.items()]
