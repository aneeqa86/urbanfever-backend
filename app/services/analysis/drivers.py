"""Drivers of heat (spectral indices, land cover) and urban growth vs warming (GHSL built-up change)."""
import ee
import numpy as np

from app.core.cache import ttl_cache
from app.services.gee_service import (
    MNDWI_PALETTE, NDBI_PALETTE, NDVI_PALETTE, STATS_SCALE, WORLDCOVER, build_context, getinfo, built_fraction,
    composite, ghsl_epoch, land_mask, resolve_season, round_or_none, tile_url, water_mask, worldcover,
    worldcover_vis_image,
)

VARIABLES = [
    ("ndvi", "NDVI (vegetation)"),
    ("ndbi", "NDBI (built-up)"),
    ("mndwi", "MNDWI (water / moisture)"),
    ("built", "Built-up fraction (GHSL)"),
]


def _regression(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 10 or np.std(x) == 0:
        return None
    r = float(np.corrcoef(x, y)[0, 1])
    slope, intercept = np.polyfit(x, y, 1)
    return {"pearson_r": round(r, 3), "r2": round(r * r, 3), "slope": round(float(slope), 3),
            "intercept": round(float(intercept), 3), "n": int(len(x))}


@ttl_cache()
def drivers(city: str, year: int, start_month: int | None, end_month: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    img = composite(ctx.study_area, year, season, with_sr=True).clip(ctx.study_area)
    land = land_mask()
    stack = (
        img.select(["LST", "NDVI", "NDBI", "MNDWI"], ["lst", "ndvi", "ndbi", "mndwi"])
        .addBands(built_fraction(year))
        .updateMask(land)
    )
    samples_fc = stack.sample(region=ctx.boundary, scale=STATS_SCALE, projection=ctx.crs, numPixels=5000,
                              seed=42, geometries=False, tileScale=4).limit(1500)

    wc = worldcover()
    lc_reducer = (
        ee.Reducer.percentile([5, 25, 50, 75, 95])
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
        .group(groupField=1, groupName="cls")
    )
    lc_stats = img.select("LST").addBands(wc).reduceRegion(
        lc_reducer, ctx.boundary, crs=ctx.crs, scale=STATS_SCALE, maxPixels=1e10, tileScale=4
    )
    info = {"rows": getinfo(samples_fc), "lc": getinfo(lc_stats)}

    rows = [f["properties"] for f in info["rows"]["features"]]
    lst = [r.get("lst") for r in rows]
    correlations = []
    for key, label in VARIABLES:
        reg = _regression([r.get(key) for r in rows], lst)
        if reg:
            correlations.append({"variable": key, "label": label, **reg})

    lc_lookup = {v: (label, color) for v, label, color in WORLDCOVER}
    pixel_km2 = (STATS_SCALE / 1000) ** 2
    by_lc = []
    for g in info["lc"].get("groups", []):
        cls = int(g["cls"])
        if cls not in lc_lookup or g.get("count", 0) < 20:
            continue
        label, color = lc_lookup[cls]
        by_lc.append({
            "class": cls, "label": label, "color": color,
            "p5": round_or_none(g.get("p5")), "p25": round_or_none(g.get("p25")), "median": round_or_none(g.get("p50")),
            "p75": round_or_none(g.get("p75")), "p95": round_or_none(g.get("p95")), "mean": round_or_none(g.get("mean")),
            "area_km2": round(g["count"] * pixel_km2, 1),
        })
    by_lc.sort(key=lambda d: -(d["median"] or -99))

    step = max(1, len(rows) // 600)
    samples = [
        {k: round_or_none(r.get(k), 3) for k in ("lst", "ndvi", "ndbi", "mndwi", "built")}
        for r in rows[::step]
    ]

    vis = {
        "ndvi": {"min": -0.1, "max": 0.7, "palette": NDVI_PALETTE},
        "ndbi": {"min": -0.4, "max": 0.3, "palette": NDBI_PALETTE},
        "mndwi": {"min": -0.6, "max": 0.6, "palette": MNDWI_PALETTE},
    }
    layers = {
        k: {"tile_url": tile_url(img.select(k.upper()), v), "vis": v} for k, v in vis.items()
    }
    layers["landcover"] = {
        "tile_url": tile_url(worldcover_vis_image().clip(ctx.study_area),
                             {"min": 0, "max": len(WORLDCOVER) - 1, "palette": [c.lstrip("#") for _, _, c in WORLDCOVER]}),
        "legend": [{"value": v, "label": l, "color": c} for v, l, c in WORLDCOVER],
        "source": "ESA WorldCover 10 m v200 (2021)",
    }

    return {
        "year": year,
        "season": season.as_dict(),
        "layers": layers,
        "samples": samples,
        "correlations": correlations,
        "lst_by_landcover": by_lc,
        "params": {
            "sample_scale_m": STATS_SCALE,
            "n_samples": len(rows),
            "indices": "NDVI=(NIR−Red)/(NIR+Red); NDBI=(SWIR1−NIR)/(SWIR1+NIR); MNDWI=(Green−SWIR1)/(Green+SWIR1), "
                       "from the seasonal surface-reflectance median",
            "landcover_note": "Land cover is ESA WorldCover 2021; for older years it is indicative only.",
        },
    }


CHANGE_CATEGORIES = [
    (1, "veg_to_built", "Vegetation → built-up", "#d7301f"),
    (2, "other_to_built", "Bare / other → built-up", "#fc8d59"),
    (3, "veg_loss", "Vegetation loss, not built", "#fdcc8a"),
    (4, "veg_gain", "Greening", "#8fd694"),
    (5, "stable_built", "Built-up, unchanged", "#6e6a78"),
    (6, "stable_other", "Non-built, unchanged", "#4a4458"),
]
NEW_BUILT_DELTA = 0.15
VEG_NDVI = 0.3
NDVI_CHANGE = 0.1


@ttl_cache()
def landchange(city: str, year1: int, year2: int, start_month: int | None, end_month: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    region = ctx.study_area
    c1 = composite(region, year1, season, with_sr=True)
    c2 = composite(region, year2, season, with_sr=True)
    b1, b2 = built_fraction(year1), built_fraction(year2)
    db = b2.subtract(b1)
    n1, n2 = c1.select("NDVI"), c2.select("NDVI")
    dn = n2.subtract(n1)
    dlst = c2.select("LST").subtract(c1.select("LST")).rename("dlst")

    cat = (
        ee.Image(6)
        .where(b2.gte(0.2), 5)
        .where(dn.gte(NDVI_CHANGE), 4)
        .where(dn.lte(-NDVI_CHANGE), 3)
        .where(db.gte(NEW_BUILT_DELTA).And(n1.lt(VEG_NDVI)), 2)
        .where(db.gte(NEW_BUILT_DELTA).And(n1.gte(VEG_NDVI)), 1)
        .updateMask(water_mask().Not())
        .updateMask(n1.mask().And(n2.mask()))
        .clip(ctx.boundary)
        .rename("cat")
        .toInt()
    )

    area = ee.Image.pixelArea().divide(1e6).rename("area")
    kw = dict(crs=ctx.crs, scale=STATS_SCALE, maxPixels=1e10, tileScale=4)
    grouped = area.addBands(dlst).addBands(cat).reduceRegion(
        ee.Reducer.sum().combine(ee.Reducer.mean(), sharedInputs=False).group(groupField=2, groupName="cat"),
        ctx.boundary, **kw,
    )
    built_area = ee.Dictionary({
        "y1": b1.multiply(area).rename("a").reduceRegion(ee.Reducer.sum(), ctx.boundary, **kw).get("a"),
        "y2": b2.multiply(area).rename("a").reduceRegion(ee.Reducer.sum(), ctx.boundary, **kw).get("a"),
    })
    info = getinfo(ee.Dictionary({"groups": grouped, "built": built_area}))
    by_cat = {int(g["cat"]): g for g in info["groups"].get("groups", [])}

    categories = []
    for v, key, label, color in CHANGE_CATEGORIES:
        g = by_cat.get(v, {})
        categories.append({"value": v, "key": key, "label": label, "color": color,
                           "area_km2": round(g.get("sum", 0), 2), "mean_dlst": round_or_none(g.get("mean"))})

    ba1, ba2 = info["built"]["y1"] or 0, info["built"]["y2"] or 0
    e1, e2 = ghsl_epoch(year1), ghsl_epoch(year2)
    v2b = next(c for c in categories if c["key"] == "veg_to_built")
    stable = next(c for c in categories if c["key"] == "stable_other")
    if v2b["area_km2"] > 0.1 and v2b["mean_dlst"] is not None:
        rel = ""
        if stable["mean_dlst"] is not None:
            rel = f" ({v2b['mean_dlst'] - stable['mean_dlst']:+.1f} °C relative to unchanged non-built land)"
        headline = (f"{v2b['area_km2']:.1f} km² of vegetation became built-up between {year1} and {year2}; "
                    f"it warmed by {v2b['mean_dlst']:+.1f} °C on average{rel}.")
    elif e1 == e2:
        headline = f"Both years map to the same GHSL epoch ({e1}), so only vegetation change is detectable."
    else:
        headline = "Little vegetation-to-built-up conversion detected inside the boundary."

    vis = {"min": 1, "max": 6, "palette": [c[3].lstrip("#") for c in CHANGE_CATEGORIES]}
    return {
        "year1": year1, "year2": year2,
        "epochs": {"year1": e1, "year2": e2},
        "tile_url": tile_url(cat, vis),
        "categories": categories,
        "built_area_km2": {
            "year1": round(ba1, 1), "year2": round(ba2, 1),
            "change_pct": round(100 * (ba2 - ba1) / ba1, 1) if ba1 else None,
        },
        "headline": headline,
        "params": {
            "built_source": "JRC GHSL GHS-BUILT-S R2023A (100 m, 5-yearly epochs; nearest epoch ≤ 2020 used)",
            "new_built_threshold": f"built-up fraction increase ≥ {NEW_BUILT_DELTA:.0%}",
            "vegetation_threshold": f"NDVI ≥ {VEG_NDVI} in year 1",
            "ndvi_change_threshold": f"|ΔNDVI| ≥ {NDVI_CHANGE}",
            "precedence": "new built-up > vegetation loss/gain > stable built-up > stable non-built",
        },
    }
