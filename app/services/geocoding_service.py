"""City geocoding with administrative boundary polygons (OpenStreetMap / Nominatim)."""
import math

import httpx

from app.core.cache import ttl_cache
from app.core.config import settings

MAX_PART_DISTANCE_KM = 60  # drop far-away islands/exclaves of a MultiPolygon


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _ring_centroid(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(ys) / len(ys), sum(xs) / len(xs)


def _filter_parts(geometry: dict, lat: float, lon: float) -> dict:
    """Keep only polygon parts near the city centre (e.g. Tokyo's Ogasawara islands are removed)."""
    if geometry["type"] != "MultiPolygon":
        return geometry
    kept = []
    for poly in geometry["coordinates"]:
        clat, clon = _ring_centroid(poly[0])
        if _haversine_km(lat, lon, clat, clon) <= MAX_PART_DISTANCE_KM:
            kept.append(poly)
    if not kept:
        kept = [max(geometry["coordinates"], key=lambda p: len(p[0]))]
    if len(kept) == 1:
        return {"type": "Polygon", "coordinates": kept[0]}
    return {"type": "MultiPolygon", "coordinates": kept}


def _bbox_polygon(b):
    min_lon, min_lat, max_lon, max_lat = b
    return {
        "type": "Polygon",
        "coordinates": [[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]],
    }


@ttl_cache(ttl=7 * 24 * 3600)
def geocode_city(city: str) -> dict:
    params = {
        "q": city,
        "format": "jsonv2",
        "limit": 5,
        "polygon_geojson": 1,
        "polygon_threshold": 0.0005,
        "accept-language": "en",
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.NOMINATIM_URL, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    if not data:
        raise ValueError(f"City '{city}' not found")

    # Prefer a result that carries a real area geometry (administrative boundary / place polygon)
    chosen = next(
        (r for r in data if r.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")),
        data[0],
    )
    lat, lon = float(chosen["lat"]), float(chosen["lon"])
    bb = chosen.get("boundingbox")  # [minLat, maxLat, minLon, maxLon]
    bbox = [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])]

    geom = chosen.get("geojson", {})
    if geom.get("type") in ("Polygon", "MultiPolygon"):
        geometry = _filter_parts(geom, lat, lon)
        source = "osm_polygon"
    else:
        # Point result (e.g. a village) — build a ~6 km box around it
        d = 0.03
        bbox = [lon - d, lat - d, lon + d, lat + d] if (bbox[2] - bbox[0]) < 0.01 else bbox
        geometry = _bbox_polygon(bbox)
        source = "bbox_fallback"

    display = chosen.get("display_name", city)
    return {
        "name": chosen.get("name") or display.split(",")[0].strip(),
        "display_name": display,
        "lat": lat,
        "lon": lon,
        "bbox": bbox,
        "geometry": geometry,
        "boundary_source": source,
        "osm_type": chosen.get("osm_type"),
        "osm_id": chosen.get("osm_id"),
    }
