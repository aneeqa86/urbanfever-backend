"""Overview: per-year LST composites, SUHI intensity, real histograms, ΔLST and data-quality metadata."""
import ee

from app.core.cache import ttl_cache
from app.services.gee_service import (
    DIVERGING_PALETTE, LST_PALETTE, STATS_SCALE, build_context, getinfo, collection_meta, composite, land_mask,
    resolve_season, round_or_none, rural_mask, sensors_for_year, tile_url, urban_mask,
)

HIST_MIN, HIST_MAX, HIST_STEPS = 0, 70, 140   # 0.5 °C bins


def _year_stats(ctx, img, year, season):
    lst = img.select("LST").updateMask(land_mask())
    reducer = (
        ee.Reducer.mean()
        .combine(ee.Reducer.stdDev(), sharedInputs=True)
        .combine(ee.Reducer.percentile([2, 98]), sharedInputs=True)
        .combine(ee.Reducer.fixedHistogram(HIST_MIN, HIST_MAX, HIST_STEPS), sharedInputs=True)
    )
    kw = dict(scale=STATS_SCALE, crs=ctx.crs, maxPixels=1e10, tileScale=4)
    city = lst.reduceRegion(reducer, ctx.boundary, **kw)
    urban = lst.updateMask(urban_mask(ctx, year)).reduceRegion(ee.Reducer.mean(), ctx.boundary, **kw).get("LST")
    rural = lst.updateMask(rural_mask(ctx, year)).reduceRegion(ee.Reducer.mean(), ctx.ring, **kw).get("LST")
    obs = img.select("obs").reduceRegion(ee.Reducer.mean(), ctx.boundary, scale=300, crs=ctx.crs, maxPixels=1e9).get("obs")
    return ee.Dictionary({
        "city": city, "urban": urban, "rural": rural, "obs": obs,
        "meta": collection_meta(ctx.study_area, year, season),
    })


def _histogram(raw):
    if not raw:
        return None
    return {"bins": [round(b[0], 2) for b in raw], "counts": [int(b[1]) for b in raw]}


def _trim_histograms(hists):
    """Trim both histograms to the common non-empty temperature range."""
    valid = [h for h in hists if h]
    if not valid:
        return hists
    idx = [i for h in valid for i, c in enumerate(h["counts"]) if c > 0]
    if not idx:
        return hists
    lo, hi = max(min(idx) - 1, 0), min(max(idx) + 2, len(valid[0]["bins"]))
    return [{"bins": h["bins"][lo:hi], "counts": h["counts"][lo:hi]} if h else None for h in hists]


@ttl_cache()
def overview(city: str, year1: int, year2: int, start_month: int | None, end_month: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    region = ctx.study_area
    imgs = {y: composite(region, y, season, with_sr=False).clip(region) for y in (year1, year2)}

    delta = imgs[year2].select("LST").subtract(imgs[year1].select("LST")).rename("dLST").updateMask(land_mask())
    kw = dict(scale=STATS_SCALE, crs=ctx.crs, maxPixels=1e10, tileScale=4)
    dstats = delta.reduceRegion(
        ee.Reducer.mean().combine(ee.Reducer.percentile([2, 98]), sharedInputs=True), ctx.boundary, **kw
    )
    warmed = delta.gt(0).rename("w").reduceRegion(ee.Reducer.mean(), ctx.boundary, **kw).get("w")

    info = getinfo(ee.Dictionary({
        "y1": _year_stats(ctx, imgs[year1], year1, season),
        "y2": _year_stats(ctx, imgs[year2], year2, season),
        "delta": dstats,
        "warmed": warmed,
    }))

    # Shared, data-driven stretch so both years are visually comparable
    p2 = [info[k]["city"].get("LST_p2") for k in ("y1", "y2") if info[k]["city"].get("LST_p2") is not None]
    p98 = [info[k]["city"].get("LST_p98") for k in ("y1", "y2") if info[k]["city"].get("LST_p98") is not None]
    vmin = round(min(p2), 1) if p2 else 20
    vmax = round(max(p98), 1) if p98 else 50
    if vmax - vmin < 2:
        vmax = vmin + 2
    vis = {"min": vmin, "max": vmax, "palette": LST_PALETTE}

    hists = _trim_histograms([_histogram(info[k]["city"].get("LST_histogram")) for k in ("y1", "y2")])

    years = []
    for key, year, hist in (("y1", year1, hists[0]), ("y2", year2, hists[1])):
        s = info[key]
        urban, rural = round_or_none(s["urban"]), round_or_none(s["rural"])
        _, warnings = sensors_for_year(year)
        scenes = s["meta"]["scene_count"]
        if scenes == 0:
            warnings = warnings + ["No cloud-free Landsat scenes for this season — try another year or a custom season."]
        elif scenes < 3:
            warnings = warnings + [f"Only {scenes} scene(s) in the season: composite is sensitive to individual days."]
        years.append({
            "year": year,
            "tile_url": tile_url(imgs[year].select("LST"), vis),
            "count_tile_url": tile_url(imgs[year].select("obs"), {"min": 0, "max": 20, "palette": ["000000", "3b0f70", "8c2981", "de4968", "fe9f6d", "fcfdbf"]}),
            "sensors": sorted(s["meta"]["sensors"]),
            "scene_count": scenes,
            "valid_obs_mean": round_or_none(s["obs"], 1),
            "lst_city_mean": round_or_none(s["city"].get("LST_mean")),
            "lst_city_std": round_or_none(s["city"].get("LST_stdDev")),
            "urban_mean": urban,
            "rural_mean": rural,
            "suhi": round(urban - rural, 2) if urban is not None and rural is not None else None,
            "histogram": hist,
            "warnings": warnings,
        })

    d = info["delta"]
    dp2, dp98 = d.get("dLST_p2"), d.get("dLST_p98")
    span = max(abs(dp2 or 0), abs(dp98 or 0), 1.0)
    span = round(min(span, 10), 1)
    dvis = {"min": -span, "max": span, "palette": DIVERGING_PALETTE}
    suhi_change = (
        round(years[1]["suhi"] - years[0]["suhi"], 2)
        if years[0]["suhi"] is not None and years[1]["suhi"] is not None else None
    )
    urban_change = (
        round(years[1]["urban_mean"] - years[0]["urban_mean"], 2)
        if years[0]["urban_mean"] is not None and years[1]["urban_mean"] is not None else None
    )

    return {
        **ctx.public(),
        "season": season.as_dict(),
        "vis": vis,
        "years": years,
        "delta": {
            "tile_url": tile_url(delta, dvis),
            "vis": dvis,
            "mean": round_or_none(d.get("dLST_mean")),
            "urban_change": urban_change,
            "suhi_change": suhi_change,
            "pct_area_warmed": round_or_none((info["warmed"] or 0) * 100, 1) if info["warmed"] is not None else None,
        },
        "params": {
            "stats_scale_m": STATS_SCALE,
            "crs": ctx.crs,
            "cloud_mask": "QA_PIXEL bits 0–5 (fill, dilated cloud, cirrus, cloud, shadow, snow)",
            "urban_definition": "GHSL built-up surface fraction ≥ 20 %, excluding water",
            "rural_definition": "1–10 km ring, built-up < 5 %, not water, elevation within ±100 m of city median",
        },
    }
