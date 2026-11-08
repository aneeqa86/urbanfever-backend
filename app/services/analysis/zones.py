"""
Neighbourhood analysis on a hexagon grid:
zonal statistics, Heat Vulnerability Index (HVI) and Geographically Weighted Regression (GWR).
"""
import math

import ee
import numpy as np

from app.core.cache import ttl_cache
from app.services.gee_service import (
    STATS_SCALE, build_context, getinfo, built_fraction, composite, land_mask, population_density, resolve_season,
)

TARGET_HEXES = 260
MAX_HEXES = 700
GWR_NEIGHBOURS = 30


# ─────────────────────────── geometry helpers ───────────────────────────

def _polygons(geojson):
    if geojson["type"] == "Polygon":
        return [geojson["coordinates"]]
    if geojson["type"] == "MultiPolygon":
        return geojson["coordinates"]
    if geojson["type"] == "GeometryCollection":
        return [p for g in geojson["geometries"] for p in _polygons(g)]
    return []


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _in_geometry(lon, lat, polys):
    for poly in polys:
        if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def hex_grid(boundary_geojson: dict, lat0: float, lon0: float, area_km2: float):
    """Pointy-top hexagons in a local equirectangular metric frame; keep those touching the boundary."""
    polys = _polygons(boundary_geojson)
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 110.57
    xs = [p[0] for poly in polys for p in poly[0]]
    ys = [p[1] for poly in polys for p in poly[0]]

    side = math.sqrt(2 * (area_km2 / TARGET_HEXES) / (3 * math.sqrt(3)))
    side = min(max(side, 0.25), 3.0)

    while True:
        w, h = math.sqrt(3) * side, 1.5 * side
        x_min, x_max = (min(xs) - lon0) * kx - w, (max(xs) - lon0) * kx + w
        y_min, y_max = (min(ys) - lat0) * ky - h, (max(ys) - lat0) * ky + h
        hexes = []
        row = 0
        y = y_min
        while y <= y_max:
            x = x_min + (w / 2 if row % 2 else 0)
            while x <= x_max:
                verts = [(x + side * math.cos(math.radians(30 + 60 * k)), y + side * math.sin(math.radians(30 + 60 * k)))
                         for k in range(6)]
                ll = [[lon0 + vx / kx, lat0 + vy / ky] for vx, vy in verts]
                c_lon, c_lat = lon0 + x / kx, lat0 + y / ky
                if _in_geometry(c_lon, c_lat, polys) or sum(_in_geometry(p[0], p[1], polys) for p in ll) >= 2:
                    hexes.append({"centroid": (c_lon, c_lat), "xy": (x, y), "ring": ll + [ll[0]]})
                x += w
            y += h
            row += 1
        if len(hexes) <= MAX_HEXES:
            return hexes, side
        side *= 1.25


# ─────────────────────────── statistics helpers ─────────────────────────

def _minmax(a):
    a = np.asarray(a, float)
    lo, hi = np.nanmin(a), np.nanmax(a)
    return np.zeros_like(a) if hi - lo == 0 else (a - lo) / (hi - lo)


def gwr(xy, y, X, k=GWR_NEIGHBOURS):
    """Adaptive bisquare-kernel GWR. Returns local coefficients (n × p) and local R²."""
    n, p = X.shape
    k = min(k, n - 1)
    d_all = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    betas = np.full((n, p), np.nan)
    r2 = np.full(n, np.nan)
    for i in range(n):
        d = d_all[i]
        bw = np.sort(d)[k] or 1e-9
        w = np.where(d < bw, (1 - (d / bw) ** 2) ** 2, 0.0)
        sw = np.sqrt(w)[:, None]
        try:
            beta, *_ = np.linalg.lstsq(X * sw, y * sw[:, 0], rcond=None)
        except np.linalg.LinAlgError:
            continue
        betas[i] = beta
        yhat = X @ beta
        ybar = np.average(y, weights=w)
        ss_tot = (w * (y - ybar) ** 2).sum()
        r2[i] = 1 - (w * (y - yhat) ** 2).sum() / ss_tot if ss_tot > 0 else np.nan
    return betas, r2


def _ols_r2(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    return float(1 - (res ** 2).sum() / ((y - y.mean()) ** 2).sum()), beta


def _r(v, nd=2):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd)


@ttl_cache()
def zones(city: str, year1: int, year2: int, start_month: int | None, end_month: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    region = ctx.study_area
    c1 = composite(region, year1, season, with_sr=False)
    c2 = composite(region, year2, season, with_sr=True)
    stack = (
        c1.select(["LST"], ["lst1"])
        .addBands(c2.select(["LST", "NDVI", "NDBI"], ["lst2", "ndvi", "ndbi"]))
        .addBands(c2.select("LST").subtract(c1.select("LST")).rename("dlst"))
        .addBands(population_density(year2))
        .addBands(built_fraction(year2))
        .updateMask(land_mask())
    )

    hexes, side = hex_grid(ctx.boundary_geojson, ctx.lat, ctx.lon, ctx.area_km2)
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Polygon([h["ring"]], None, False), {"hid": i}) for i, h in enumerate(hexes)
    ])
    reduced = stack.reduceRegions(fc, ee.Reducer.mean(), scale=STATS_SCALE, crs=ctx.crs, tileScale=4)
    props = {int(f["properties"]["hid"]): f["properties"] for f in getinfo(reduced)["features"]}

    rows = []
    for i, h in enumerate(hexes):
        p = props.get(i, {})
        if p.get("lst2") is None or p.get("ndvi") is None:
            continue
        rows.append({"hid": i, "hex": h, **{k: p.get(k) for k in ("lst1", "lst2", "dlst", "ndvi", "ndbi", "pop_density", "built")}})
    if len(rows) < 5:
        raise ValueError("Too few hexagons with valid data for a neighbourhood analysis.")

    lst2 = np.array([r["lst2"] for r in rows], float)
    ndvi = np.array([r["ndvi"] for r in rows], float)
    ndbi = np.array([r["ndbi"] if r["ndbi"] is not None else np.nan for r in rows], float)
    pop = np.array([max(r["pop_density"] or 0, 0) for r in rows], float)

    # HVI: exposure (LST) + sensitivity proxy (population density) + lack of adaptive capacity (1 − NDVI)
    hvi = (_minmax(lst2) + _minmax(np.log1p(pop)) + _minmax(1 - ndvi)) / 3
    quint = np.quantile(hvi, [0.2, 0.4, 0.6, 0.8])
    hvi_class = np.searchsorted(quint, hvi, side="right") + 1

    # GWR: LST ~ NDVI + NDBI
    ndbi_f = np.where(np.isfinite(ndbi), ndbi, np.nanmean(ndbi))
    X = np.column_stack([np.ones(len(rows)), ndvi, ndbi_f])
    xy = np.array([r["hex"]["xy"] for r in rows], float)
    global_r2, global_beta = _ols_r2(lst2, X)
    betas, local_r2 = gwr(xy, lst2, X) if len(rows) >= 12 else (np.full((len(rows), 3), np.nan), np.full(len(rows), np.nan))

    rank_lst = (-lst2).argsort().argsort() + 1
    rank_hvi = (-hvi).argsort().argsort() + 1

    features = []
    for j, r in enumerate(rows):
        features.append({
            "type": "Feature",
            "id": r["hid"],
            "geometry": {"type": "Polygon", "coordinates": [r["hex"]["ring"]]},
            "properties": {
                "id": r["hid"],
                "lst1": _r(r["lst1"]), "lst2": _r(r["lst2"]), "dlst": _r(r["dlst"]),
                "ndvi": _r(r["ndvi"], 3), "ndbi": _r(r["ndbi"], 3),
                "pop_density": _r(r["pop_density"], 0), "built": _r(r["built"], 3),
                "hvi": _r(hvi[j], 3), "hvi_class": int(hvi_class[j]),
                "hvi_components": {
                    "heat": _r(_minmax(lst2)[j], 3),
                    "population": _r(_minmax(np.log1p(pop))[j], 3),
                    "low_vegetation": _r(_minmax(1 - ndvi)[j], 3),
                },
                "gwr_intercept": _r(betas[j, 0]), "gwr_ndvi": _r(betas[j, 1]), "gwr_ndbi": _r(betas[j, 2]),
                "gwr_r2": _r(local_r2[j], 3),
                "rank_lst": int(rank_lst[j]), "rank_hvi": int(rank_hvi[j]),
            },
        })

    order_hot = [features[j]["id"] for j in np.argsort(-lst2)[:10]]
    order_hvi = [features[j]["id"] for j in np.argsort(-hvi)[:10]]
    return {
        "year1": year1, "year2": year2,
        "season": season.as_dict(),
        "hex_size_m": round(side * 1000),
        "hex_area_km2": round(1.5 * math.sqrt(3) * side ** 2, 3),
        "features": {"type": "FeatureCollection", "features": features},
        "summary": {
            "n": len(features),
            "top_hot": order_hot,
            "top_hvi": order_hvi,
            "global_ols_r2": round(global_r2, 3),
            "global_ols_coefficients": {"intercept": _r(global_beta[0]), "ndvi": _r(global_beta[1]), "ndbi": _r(global_beta[2])},
            "gwr_mean_r2": _r(np.nanmean(local_r2), 3),
            "bandwidth_neighbours": min(GWR_NEIGHBOURS, len(rows) - 1),
        },
        "hvi_method": "HVI = mean of min–max normalised LST (exposure), log population density (sensitivity) "
                      "and 1 − NDVI (lack of green cover); classified into quintiles (1 = low … 5 = very high).",
        "gwr_method": "GWR of LST ~ NDVI + NDBI across hexagon centroids, adaptive bisquare kernel "
                      f"({min(GWR_NEIGHBOURS, len(rows) - 1)} nearest neighbours).",
        "params": {"population_source": "JRC GHS-POP R2023A (nearest 5-year epoch)", "zonal_scale_m": STATS_SCALE},
    }
