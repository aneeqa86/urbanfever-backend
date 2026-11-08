"""Getis-Ord Gi* hot/cold spot analysis of LST, computed per pixel in a fixed UTM grid."""
import ee

from app.core.cache import ttl_cache
from app.services.gee_service import build_context, getinfo, composite, land_mask, resolve_season, tile_url

SCALE_M = 120
KERNEL_RADIUS_PX = 5   # 600 m neighbourhood

CLASSES = [
    (3, "Hot spot 99 %", "#e8412f"),
    (2, "Hot spot 95 %", "#f58a5e"),
    (1, "Hot spot 90 %", "#f9c3a6"),
    (0, "Not significant", "#3a3545"),
    (-1, "Cold spot 90 %", "#a9d6ea"),
    (-2, "Cold spot 95 %", "#5aa9d6"),
    (-3, "Cold spot 99 %", "#2a6fb8"),
]


def gi_star_classes(ctx, lst: ee.Image) -> ee.Image:
    """
    Gi* = (Σ w·x − X̄·Σw) / (S · sqrt((n·Σw² − (Σw)²) / (n − 1)))   with binary weights (Σw² = Σw).
    Masked pixels contribute neither to the sums nor to the neighbour counts.
    """
    proj = dict(crs=ctx.crs, scale=SCALE_M)
    x = lst.clip(ctx.boundary).updateMask(land_mask()).reproject(**proj).rename("x")
    stats = x.reduceRegion(
        ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True).combine(ee.Reducer.count(), sharedInputs=True),
        ctx.boundary, maxPixels=1e10, tileScale=4, **proj,
    )
    mean = ee.Number(stats.get("x_mean"))
    sd = ee.Number(stats.get("x_stdDev"))
    n = ee.Number(stats.get("x_count"))

    k = ee.Kernel.circle(KERNEL_RADIUS_PX, "pixels", False)
    valid = x.mask().gt(0).unmask(0).toFloat()
    sum_wx = x.unmask(0).convolve(k).reproject(**proj)
    sum_w = valid.convolve(k).reproject(**proj)
    num = sum_wx.subtract(sum_w.multiply(mean))
    den = sum_w.multiply(n).subtract(sum_w.pow(2)).divide(n.subtract(1)).sqrt().multiply(sd)
    z = num.divide(den).updateMask(x.mask()).rename("z")

    cls = (
        ee.Image(0)
        .where(z.gt(1.645), 1).where(z.gt(1.96), 2).where(z.gt(2.576), 3)
        .where(z.lt(-1.645), -1).where(z.lt(-1.96), -2).where(z.lt(-2.576), -3)
        .updateMask(z.mask())
        .rename("cls")
        .toInt()
    )
    return cls, z


@ttl_cache()
def hotspots(city: str, year: int, start_month: int | None, end_month: int | None) -> dict:
    ctx = build_context(city)
    season = resolve_season(ctx.lat, start_month, end_month)
    lst = composite(ctx.study_area, year, season, with_sr=False).select("LST")
    cls, _ = gi_star_classes(ctx, lst)

    area = ee.Image.pixelArea().divide(1e6).rename("area")
    grouped = getinfo(area.addBands(cls.add(3)).reduceRegion(  # shift to 0..6 (group field must be ≥ 0)
        ee.Reducer.sum().group(groupField=1, groupName="cls"),
        ctx.boundary, crs=ctx.crs, scale=SCALE_M, maxPixels=1e10, tileScale=4,
    ))
    areas = {int(g["cls"]) - 3: g["sum"] for g in grouped.get("groups", [])}
    total = sum(areas.values()) or 1

    vis = {"min": -3, "max": 3, "palette": [c[2].lstrip("#") for c in reversed(CLASSES)]}
    return {
        "year": year,
        "season": season.as_dict(),
        "tile_url": tile_url(cls, vis),
        "classes": [
            {"value": v, "label": label, "color": color,
             "area_km2": round(areas.get(v, 0), 2), "pct": round(100 * areas.get(v, 0) / total, 1)}
            for v, label, color in CLASSES
        ],
        "params": {
            "statistic": "Getis-Ord Gi*",
            "kernel_radius_m": KERNEL_RADIUS_PX * SCALE_M,
            "scale_m": SCALE_M,
            "weights": "binary, circular neighbourhood including the focal pixel",
            "crs": ctx.crs,
            "thresholds": "|z| > 1.645 (90 %), 1.96 (95 %), 2.576 (99 %)",
        },
    }
