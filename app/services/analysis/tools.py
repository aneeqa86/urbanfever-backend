"""Interactive tools: transect profile, pixel inspector, GeoTIFF export."""
import math

import ee

from app.core.cache import ttl_cache
from app.services.gee_service import (
    WORLDCOVER, build_context, built_fraction, composite, getinfo, last_complete_year, population_density,
    resolve_season, round_or_none, sensors_for_year, worldcover,
)

TRANSECT_POINTS = 120


def _hav_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = p2 - p1, math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _densify(coords, n):
    seg = [_hav_km(coords[i], coords[i + 1]) for i in range(len(coords) - 1)]
    total = sum(seg)
    if total == 0:
        raise ValueError("Transect has zero length.")
    pts, cum, i = [], 0.0, 0
    for k in range(n):
        d = total * k / (n - 1)
        while i < len(seg) - 1 and cum + seg[i] < d:
            cum += seg[i]
            i += 1
        t = 0 if seg[i] == 0 else min(max((d - cum) / seg[i], 0), 1)
        a, b = coords[i], coords[i + 1]
        pts.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), d))
    return pts, total


def transect(coords, year1, year2, start_month, end_month):
    if len(coords) < 2:
        raise ValueError("A transect needs at least two points.")
    lat = sum(c[1] for c in coords) / len(coords)
    season = resolve_season(lat, start_month, end_month)
    line = ee.Geometry.LineString(coords).buffer(500)
    c1 = composite(line, year1, season, with_sr=False)
    c2 = composite(line, year2, season, with_sr=True)
    stack = (
        c1.select(["LST"], ["lst1"])
        .addBands(c2.select(["LST", "NDVI"], ["lst2", "ndvi"]))
        .addBands(built_fraction(year2))
    )
    pts, total = _densify(coords, TRANSECT_POINTS)
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([x, y]), {"i": k}) for k, (x, y, _) in enumerate(pts)])
    rows = getinfo(stack.reduceRegions(fc, ee.Reducer.first(), scale=30))["features"]
    by_i = {int(f["properties"]["i"]): f["properties"] for f in rows}

    out = {"distance_km": [], "lst1": [], "lst2": [], "ndvi": [], "built": [], "points": []}
    for k, (x, y, d) in enumerate(pts):
        p = by_i.get(k, {})
        out["distance_km"].append(round(d, 3))
        out["lst1"].append(round_or_none(p.get("lst1")))
        out["lst2"].append(round_or_none(p.get("lst2")))
        out["ndvi"].append(round_or_none(p.get("ndvi"), 3))
        out["built"].append(round_or_none(p.get("built"), 3))
        out["points"].append([round(x, 6), round(y, 6)])
    return {"year1": year1, "year2": year2, "season": season.as_dict(), "length_km": round(total, 3), **out}


@ttl_cache(ttl=3600)
def pixel(lat, lon, year1, year2, start_month, end_month):
    season = resolve_season(lat, start_month, end_month)
    pt = ee.Geometry.Point([lon, lat])
    area = pt.buffer(45)
    c1 = composite(area, year1, season, with_sr=False)
    c2 = composite(area, year2, season, with_sr=True)
    stack = (
        c1.select(["LST"], ["lst1"])
        .addBands(c2.select(["LST", "NDVI", "NDBI", "MNDWI"], ["lst2", "ndvi", "ndbi", "mndwi"]))
        .addBands(worldcover())
        .addBands(built_fraction(year2))
        .addBands(population_density(year2))
    )
    values = stack.reduceRegion(ee.Reducer.first(), pt, 30)

    years = list(range(1985, last_complete_year() + 1))
    feats = [
        ee.Feature(None, {
            "year": y,
            "lst": ee.Dictionary(composite(area, y, season, with_sr=False).select("LST")
                                 .reduceRegion(ee.Reducer.mean(), area, 30)).get("LST", -9999),
        })
        for y in years
    ]
    v = getinfo(values)
    by_year = {}
    for i in range(0, len(feats), 10):   # properties keep the year → no misalignment when a year is empty
        for f in getinfo(ee.FeatureCollection(feats[i:i + 10]))["features"]:
            by_year[int(f["properties"]["year"])] = f["properties"].get("lst")
    info = {"series": [by_year.get(y) for y in years]}
    lc = {val: label for val, label, _ in WORLDCOVER}.get(v.get("landcover"))
    lst1, lst2 = round_or_none(v.get("lst1")), round_or_none(v.get("lst2"))
    series = [
        {"year": y, "lst": None if s is None or s == -9999 else round(s, 2),
         "sensor": "+".join(x.replace("LANDSAT_", "L") for x in sensors_for_year(y)[0])}
        for y, s in zip(years, info["series"])
    ]
    return {
        "lat": lat, "lon": lon, "season": season.as_dict(),
        "values": {
            "lst1": lst1, "lst2": lst2,
            "dlst": round(lst2 - lst1, 2) if lst1 is not None and lst2 is not None else None,
            "ndvi": round_or_none(v.get("NDVI") if "NDVI" in v else v.get("ndvi"), 3),
            "ndbi": round_or_none(v.get("ndbi"), 3),
            "mndwi": round_or_none(v.get("mndwi"), 3),
            "landcover": v.get("landcover"), "landcover_label": lc,
            "built": round_or_none(v.get("built"), 3),
            "population_density": round_or_none(v.get("pop_density"), 0),
        },
        "series": series,
    }


EXPORT_LAYERS = {
    "lst_y1": "Land surface temperature, year 1 (°C)",
    "lst_y2": "Land surface temperature, year 2 (°C)",
    "delta": "ΔLST year 2 − year 1 (°C)",
    "ndvi": "NDVI, year 2",
    "hotspots": "Getis-Ord Gi* classes, year 2 (−3…3)",
    "trend_slope": "Sen's slope of LST (°C / decade)",
}


def export(city, year1, year2, start_month, end_month, layer):
    if layer not in EXPORT_LAYERS:
        raise ValueError(f"Unknown layer '{layer}'. Choose one of: {', '.join(EXPORT_LAYERS)}")
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    region = ctx.study_area
    scale = 100
    if layer in ("lst_y1", "lst_y2", "delta", "ndvi"):
        c1 = composite(region, year1, season, with_sr=False)
        c2 = composite(region, year2, season, with_sr=layer == "ndvi")
        img = {
            "lst_y1": lambda: c1.select("LST"),
            "lst_y2": lambda: c2.select("LST"),
            "delta": lambda: c2.select("LST").subtract(c1.select("LST")).rename("dLST"),
            "ndvi": lambda: c2.select("NDVI"),
        }[layer]()
    elif layer == "hotspots":
        from app.services.analysis.hotspots import SCALE_M, gi_star_classes

        img, _ = gi_star_classes(ctx, composite(region, year2, season, with_sr=False).select("LST"))
        scale = SCALE_M
    else:
        from app.services.analysis.trend import trend_images

        img, _, _ = trend_images(ctx, list(range(1985, last_complete_year() + 1)), season)
        scale = 200

    # keep the request under EE's 32 MB direct-download limit (float32)
    w_km = (ctx.map_bounds[2] - ctx.map_bounds[0]) * 111 * math.cos(math.radians(ctx.lat))
    h_km = (ctx.map_bounds[3] - ctx.map_bounds[1]) * 111
    while (w_km * 1000 / scale) * (h_km * 1000 / scale) * 4 > 30e6:
        scale *= 1.5
    safe = "".join(ch if ch.isalnum() else "_" for ch in ctx.city.lower())
    name = f"urbanfever_{safe}_{layer}_{year1}_{year2}"
    url = img.toFloat().clip(ctx.boundary).getDownloadURL({
        "name": name, "scale": round(scale), "crs": ctx.crs, "region": ctx.boundary.bounds(100), "format": "GEO_TIFF",
    })
    return {"layer": layer, "description": EXPORT_LAYERS[layer], "url": url, "filename": f"{name}.tif",
            "scale_m": round(scale), "crs": ctx.crs}
