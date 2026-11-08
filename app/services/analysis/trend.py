"""Long-term trend: annual seasonal composites, Sen's slope + Mann-Kendall (per pixel and city-wide), ERA5-Land validation."""
import math

import ee
import numpy as np

from app.core.cache import ttl_cache
from app.services.gee_service import (
    DIVERGING_PALETTE, build_context, composite, getinfo, ghsl_epoch, land_mask, landsat_collection, last_complete_year,
    resolve_season, round_or_none, rural_mask, sensors_for_year, tile_url, urban_mask,
)

MIN_YEARS_PER_PIXEL = 8
CHUNK_YEARS = 6
NODATA = -9999


def mann_kendall(years, values):
    """Sen's slope (per year) and two-sided Mann-Kendall test with tie correction."""
    pts = [(y, v) for y, v in zip(years, values) if v is not None]
    n = len(pts)
    if n < 5:
        return None
    x = np.array([p[0] for p in pts], float)
    y = np.array([p[1] for p in pts], float)
    slopes, s = [], 0
    for i in range(n - 1):
        dx = x[i + 1:] - x[i]
        dy = y[i + 1:] - y[i]
        slopes.extend((dy / dx).tolist())
        s += int(np.sign(dy).sum())
    _, counts = np.unique(y, return_counts=True)
    var_s = (n * (n - 1) * (2 * n + 5) - sum(c * (c - 1) * (2 * c + 5) for c in counts)) / 18
    z = 0.0 if s == 0 else (s - np.sign(s)) / math.sqrt(var_s)
    p = math.erfc(abs(z) / math.sqrt(2))
    tau = s / (n * (n - 1) / 2)
    return {
        "sen_slope_per_decade": round(float(np.median(slopes)) * 10, 3),
        "kendall_tau": round(tau, 3),
        "z": round(float(z), 3),
        "p_value": round(p, 4),
        "significant": p < 0.05,
        "n_years": n,
    }


def _annual_images(ctx, years, season):
    land = land_mask()
    out = []
    for y in years:
        lst = composite(ctx.study_area, y, season, with_sr=False).select("LST").updateMask(land)
        out.append(ee.Image.constant(y).toFloat().rename("year").addBands(lst).clip(ctx.study_area))
    return out


def trend_images(ctx, years, season):
    col = ee.ImageCollection.fromImages(_annual_images(ctx, years, season))
    n = col.select("LST").count()
    slope = col.reduce(ee.Reducer.sensSlope()).select("slope").multiply(10).rename("slope")  # °C / decade
    # EE's per-pixel 'p-value' output is null for image collections, so derive the Mann-Kendall z from Kendall's tau:
    # Var(tau) = 2(2n + 5) / (9n(n − 1))  →  z = tau / sqrt(Var(tau)),  significant (p < 0.05) when |z| > 1.96
    tau = col.reduce(ee.Reducer.kendallsCorrelation(2)).select("tau")
    var_tau = n.multiply(2).add(5).multiply(2).divide(n.multiply(9).multiply(n.subtract(1)))
    z = tau.divide(var_tau.sqrt()).rename("z")
    enough = n.gte(MIN_YEARS_PER_PIXEL)
    slope = slope.updateMask(enough)
    signif = slope.updateMask(z.abs().gt(1.96))
    return slope, signif, z


@ttl_cache()
def trend(city: str, start_month: int | None, end_month: int | None, start: int | None, end: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    start = start or 1985
    end = end or last_complete_year()
    years = list(range(start, end + 1))

    era5 = ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR").select("temperature_2m")
    annual = _annual_images(ctx, years, season)
    kw = dict(scale=200, crs=ctx.crs, maxPixels=1e10, tileScale=4)
    urban_masks, rural_masks, feats = {}, {}, []
    for y, img in zip(years, annual):
        e = ghsl_epoch(y)
        if e not in urban_masks:
            urban_masks[e] = urban_mask(ctx, y)
            rural_masks[e] = rural_mask(ctx, y)
        lst = img.select("LST")
        s, en = season.date_range(y)
        air = era5.filterDate(s, en).mean().subtract(273.15)
        feats.append(ee.Feature(None, {
            "year": y,
            "scenes": landsat_collection(ctx.study_area, y, season).size(),
            "urban": ee.Dictionary(lst.updateMask(urban_masks[e]).reduceRegion(ee.Reducer.mean(), ctx.boundary, **kw)).get("LST", NODATA),
            "rural": ee.Dictionary(lst.updateMask(rural_masks[e]).reduceRegion(ee.Reducer.mean(), ctx.ring, **kw)).get("LST", NODATA),
            "air": ee.Dictionary(air.reduceRegion(ee.Reducer.mean(), ctx.boundary, scale=11132, maxPixels=1e9)).get("temperature_2m", NODATA),
        }))
    rows = []
    for i in range(0, len(feats), CHUNK_YEARS):   # chunked to stay under EE's concurrent-aggregation limit
        rows += getinfo(ee.FeatureCollection(feats[i:i + CHUNK_YEARS]))["features"]

    def clean(v):
        return None if v is None or v == NODATA else round(v, 2)

    series = []
    for f in sorted(rows, key=lambda r: r["properties"]["year"]):
        p = f["properties"]
        u, r = clean(p.get("urban")), clean(p.get("rural"))
        y = int(p["year"])
        series.append({
            "year": y,
            "urban": u,
            "rural": r,
            "suhi": round(u - r, 2) if u is not None and r is not None else None,
            "air_temp": clean(p.get("air")),
            "scenes": p.get("scenes"),
            "sensor": "+".join(s.replace("LANDSAT_", "L") for s in sensors_for_year(y)[0]),
        })

    yrs = [s["year"] for s in series]
    city_trend = mann_kendall(yrs, [s["urban"] for s in series])
    suhi_trend = mann_kendall(yrs, [s["suhi"] for s in series])
    air_trend = mann_kendall(yrs, [s["air_temp"] for s in series])

    pairs = [(s["urban"], s["air_temp"]) for s in series if s["urban"] is not None and s["air_temp"] is not None]
    validation = None
    if len(pairs) >= 5:
        a = np.array(pairs)
        validation = {
            "pearson_r": round(float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), 3),
            "mean_offset": round(float((a[:, 0] - a[:, 1]).mean()), 2),
            "n": len(pairs),
            "note": "Landsat LST is daytime (~10:30 local) land-surface skin temperature; ERA5-Land 2 m air "
                    "temperature is a ~9 km, 24-hour seasonal mean, so LST is expected to be several °C higher. "
                    "Agreement in year-to-year variation (r) is the meaningful check.",
        }

    slope, signif, _ = trend_images(ctx, years, season)
    vis = {"min": -1.5, "max": 1.5, "palette": DIVERGING_PALETTE}
    pct = None
    try:
        pct_raw = getinfo(
            signif.gt(0).unmask(0).updateMask(slope.mask()).rename("s")
            .reduceRegion(ee.Reducer.mean(), ctx.boundary, scale=300, crs=ctx.crs, maxPixels=1e9, tileScale=16)
            .get("s")
        )
        pct = round_or_none(pct_raw * 100, 1) if pct_raw is not None else None
    except ee.EEException:
        pct = None

    return {
        "start": start,
        "end": end,
        "season": season.as_dict(),
        "series": series,
        "city_trend": city_trend,
        "suhi_trend": suhi_trend,
        "air_trend": air_trend,
        "slope_tile_url": tile_url(slope, vis),
        "signif_tile_url": tile_url(signif, vis),
        "slope_vis": vis,
        "pct_area_significant_warming": pct,
        "validation": validation,
        "params": {
            "method": "Theil–Sen slope with Mann-Kendall significance (p < 0.05) on annual seasonal-mean LST",
            "min_years_per_pixel": MIN_YEARS_PER_PIXEL,
            "series_scale_m": 200,
            "note": "Sensor changes (L5 → L7 → L8/9) can introduce small step biases; Collection 2 ST products are "
                    "calibrated per sensor but not cross-harmonised.",
        },
    }
