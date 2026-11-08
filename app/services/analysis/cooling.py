"""Cooling effect of green space and water: distance-decay of LST over built-up pixels."""
import ee

from app.core.cache import ttl_cache
from app.services.gee_service import (
    build_context, built_fraction, composite, getinfo, resolve_season, round_or_none, tile_url, water_mask, worldcover,
)

MAX_DIST_M = 1500
BIN_M = 100
SCALE_M = 30


def _profile(ctx, lst, source, urban):
    proj = dict(crs=ctx.crs, scale=SCALE_M)
    dist = (
        source.unmask(0).reproject(**proj)
        .distance(ee.Kernel.euclidean(MAX_DIST_M + BIN_M, "meters"), False)
        .reproject(**proj)
    )
    bins = dist.divide(BIN_M).floor().toInt().rename("bin")
    test = urban.And(source.Not())
    stack = lst.updateMask(test).addBands(bins.updateMask(bins.lte(MAX_DIST_M // BIN_M)))
    return stack.reduceRegion(
        ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True).group(groupField=1, groupName="bin"),
        ctx.boundary, maxPixels=1e10, tileScale=8, **proj,
    )


def _summarise(groups):
    pts = sorted(
        ({"distance_m": int(g["bin"]) * BIN_M + BIN_M // 2, "lst": round_or_none(g["mean"]), "n": g["count"]}
         for g in groups if g.get("count", 0) >= 30),
        key=lambda p: p["distance_m"],
    )
    if len(pts) < 4:
        return {"profile": pts, "cooling_magnitude": None, "cooling_extent_m": None}
    near = pts[0]["lst"]
    plateau = sum(p["lst"] for p in pts[-3:]) / 3
    magnitude = round(plateau - near, 2)
    extent = None
    if magnitude > 0.1:
        target = near + 0.9 * magnitude
        extent = next((p["distance_m"] for p in pts if p["lst"] >= target), pts[-1]["distance_m"])
    return {"profile": pts, "cooling_magnitude": magnitude, "cooling_extent_m": extent}


@ttl_cache()
def cooling(city: str, year: int, start_month: int | None, end_month: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    comp = composite(ctx.study_area, year, season, with_sr=True)
    lst = comp.select("LST")
    wc = worldcover()
    water = water_mask().Or(wc.eq(80))
    # green space = tree / grass / mangrove cover that is actually vegetated in this season (NDVI ≥ 0.3),
    # which excludes dry grassland that behaves thermally like bare soil
    green = wc.eq(10).Or(wc.eq(30)).Or(wc.eq(95)).And(comp.select("NDVI").unmask(0).gte(0.3)).And(water.Not())
    urban = built_fraction(year).gte(0.2).And(water.Not()).And(green.Not())

    info = getinfo(ee.Dictionary({
        "green": _profile(ctx, lst, green, urban),
        "water": _profile(ctx, lst, water, urban),
    }))
    mask_img = ee.Image(0).where(water, 1).where(green, 2).selfMask().clip(ctx.study_area)
    return {
        "year": year,
        "season": season.as_dict(),
        "mask_tile_url": tile_url(mask_img, {"min": 1, "max": 2, "palette": ["2b8cbe", "31a354"]}),
        "mask_legend": [{"value": 2, "label": "Green space (trees, grass, mangroves)", "color": "#31a354"},
                        {"value": 1, "label": "Water", "color": "#2b8cbe"}],
        "green": _summarise(info["green"].get("groups", [])),
        "water": _summarise(info["water"].get("groups", [])),
        "params": {
            "test_pixels": "built-up (GHSL ≥ 20 %), not green, not water",
            "green_definition": "WorldCover tree / grass / mangrove with seasonal NDVI ≥ 0.3",
            "distance": f"Euclidean distance to nearest source pixel, {BIN_M} m bins up to {MAX_DIST_M} m, {SCALE_M} m UTM grid",
            "magnitude": "LST plateau (mean of last 3 bins) − LST in the first bin",
            "extent": "distance where 90 % of the cooling magnitude is recovered",
            "sources": "ESA WorldCover 2021, JRC Global Surface Water (occurrence ≥ 50 %)",
        },
    }
