"""
Core Earth Engine building blocks shared by every analysis module.

Pipeline (per year):
  Landsat Collection 2 Level-2 (L5 / L7 / L8+L9 harmonised band names)
  -> QA_PIXEL mask: fill, dilated cloud, cirrus, cloud, cloud shadow, snow
  -> scale factors (SR: *2.75e-5 - 0.2, ST: *0.00341802 + 149 K -> degC)
  -> seasonal composite (LST mean, LST temporal std, valid observation count,
     surface reflectance median -> NDVI / NDBI / MNDWI)
"""
import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

import ee

from app.core.cache import ttl_cache
from app.core.config import settings

_initialized = False

SCOPES = [
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
]


def initialize_gee():
    global _initialized
    if _initialized:
        return
    if settings.GEE_SERVICE_ACCOUNT_KEY:
        info = json.loads(settings.GEE_SERVICE_ACCOUNT_KEY)
        credentials = ee.ServiceAccountCredentials(info["client_email"], key_data=settings.GEE_SERVICE_ACCOUNT_KEY)
    elif settings.GEE_KEY_FILE and os.path.exists(settings.GEE_KEY_FILE):
        email = settings.GEE_SERVICE_ACCOUNT
        if not email:
            with open(settings.GEE_KEY_FILE, encoding="utf-8") as f:
                email = json.load(f)["client_email"]
        credentials = ee.ServiceAccountCredentials(email, settings.GEE_KEY_FILE)
    else:
        import google.auth

        credentials, _ = google.auth.default(scopes=SCOPES)
    ee.Initialize(credentials, project=settings.GEE_PROJECT_ID or None)
    _initialized = True


# ─────────────────────────────── constants ────────────────────────────────

LST_PALETTE = [
    "040274", "0502a3", "0502ce", "0602e8", "1001e3", "1e01d9", "3201d2", "4f01c9",
    "6e01be", "8e01b4", "ae01a9", "ce019e", "ee0192", "f90e82", "f91d72", "f92b62",
    "f93952", "f94742", "f95532", "f96322", "f97112", "f97f02", "f98e00", "f99c00",
    "f9aa00", "f9b800", "f9c600", "f9d400",
]
DIVERGING_PALETTE = ["2166ac", "4393c3", "92c5de", "d1e5f0", "f7f7f7", "fddbc7", "f4a582", "d6604d", "b2182b"]
NDVI_PALETTE = ["8c510a", "bf812d", "dfc27d", "f6e8c3", "c7e9c0", "74c476", "31a354", "006d2c"]
NDBI_PALETTE = ["1a9850", "91cf60", "d9ef8b", "fee08b", "fc8d59", "d73027", "7f0000"]
MNDWI_PALETTE = ["a6611a", "dfc27d", "f5f5f5", "80cdc1", "018571", "08306b"]

WORLDCOVER = [
    (10, "Tree cover", "#006400"),
    (20, "Shrubland", "#ffbb22"),
    (30, "Grassland", "#ffff4c"),
    (40, "Cropland", "#f096ff"),
    (50, "Built-up", "#fa0000"),
    (60, "Bare / sparse vegetation", "#b4b4b4"),
    (70, "Snow and ice", "#f0f0f0"),
    (80, "Permanent water", "#0064c8"),
    (90, "Herbaceous wetland", "#0096a0"),
    (95, "Mangroves", "#00cf75"),
    (100, "Moss and lichen", "#fae6a0"),
]

URBAN_BUILT_THRESHOLD = 0.20   # GHSL built-up surface fraction defining "urban" pixels
RURAL_BUILT_THRESHOLD = 0.05   # max built fraction for rural reference pixels
RURAL_RING_INNER_M = 1000
RURAL_RING_OUTER_M = 10000
RURAL_MAX_ELEV_DIFF_M = 100
STATS_SCALE = 100

_SR_BANDS = ["blue", "green", "red", "nir", "swir1", "swir2"]
_SENSORS = {
    "LANDSAT_5": ("LANDSAT/LT05/C02/T1_L2", ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7", "ST_B6", "QA_PIXEL"]),
    "LANDSAT_7": ("LANDSAT/LE07/C02/T1_L2", ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7", "ST_B6", "QA_PIXEL"]),
    "LANDSAT_8": ("LANDSAT/LC08/C02/T1_L2", ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7", "ST_B10", "QA_PIXEL"]),
    "LANDSAT_9": ("LANDSAT/LC09/C02/T1_L2", ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7", "ST_B10", "QA_PIXEL"]),
}
_COMMON = _SR_BANDS + ["ST", "QA_PIXEL"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def last_complete_year() -> int:
    return datetime.now().year - 1


# ─────────────────────────────── season ───────────────────────────────────

@dataclass(frozen=True)
class Season:
    start_month: int
    end_month: int
    mode: str

    @property
    def label(self) -> str:
        if self.start_month == 1 and self.end_month == 12:
            return "Full year"
        return f"{MONTHS[self.start_month - 1]}–{MONTHS[self.end_month - 1]}"

    def date_range(self, year: int) -> tuple[str, str]:
        """Season belonging to `year`. Seasons that wrap the new year (e.g. Nov–Mar) end in `year`."""
        if self.start_month <= self.end_month:
            start = f"{year}-{self.start_month:02d}-01"
        else:
            start = f"{year - 1}-{self.start_month:02d}-01"
        end_y, end_m = (year + 1, 1) if self.end_month == 12 else (year, self.end_month + 1)
        return start, f"{end_y}-{end_m:02d}-01"

    def as_dict(self) -> dict:
        return {"start_month": self.start_month, "end_month": self.end_month, "label": self.label, "mode": self.mode}


def resolve_season(lat: float, start_month: int | None, end_month: int | None) -> Season:
    if start_month and end_month:
        return Season(start_month, end_month, "custom")
    if lat >= 23.5:
        return Season(5, 9, "auto-north")      # northern hemisphere summer
    if lat <= -23.5:
        return Season(11, 3, "auto-south")     # southern hemisphere summer (Nov–Mar)
    return Season(1, 12, "auto-tropical")      # tropics: full year maximises cloud-free scenes


# ─────────────────────────────── sensors ──────────────────────────────────

def sensors_for_year(year: int) -> tuple[list[str], list[str]]:
    """Choose Landsat missions for a year, avoiding Landsat 7 SLC-off stripes where possible."""
    warnings = []
    if year < 1984:
        raise ValueError("No Landsat thermal data available before 1984.")
    if year >= 2013:
        sensors = ["LANDSAT_8"] + (["LANDSAT_9"] if year >= 2021 else [])
    elif year == 2012:
        sensors = ["LANDSAT_7"]
        warnings.append("2012 relies on Landsat 7 SLC-off imagery: striped data gaps reduce coverage.")
    elif 1999 <= year <= 2011:
        # Landsat 5 acquisitions are sparse outside US ground-station range (e.g. South Asia 2003–2011),
        # so Landsat 7 is merged in; after May 2003 its scenes are SLC-off but multi-date compositing fills most stripes.
        sensors = ["LANDSAT_5", "LANDSAT_7"]
        if year >= 2003:
            warnings.append("Includes Landsat 7 SLC-off scenes (striped gaps filled by multi-date compositing).")
    else:
        sensors = ["LANDSAT_5"]
    if year < 2013:
        warnings.append("Landsat 5/7 thermal band (120 m / 60 m native) is coarser than Landsat 8/9 (100 m).")
    return sensors, warnings


def _prep(image: ee.Image) -> ee.Image:
    qa = image.select("QA_PIXEL")
    # bit0 fill, bit1 dilated cloud, bit2 cirrus, bit3 cloud, bit4 cloud shadow, bit5 snow
    clear = qa.bitwiseAnd(0b111111).eq(0)
    sr = image.select(_SR_BANDS).multiply(0.0000275).add(-0.2)
    lst = image.select("ST").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST")
    valid = lst.gt(-30).And(lst.lt(75))
    return (
        sr.addBands(lst)
        .updateMask(clear.And(valid))
        .copyProperties(image, ["system:time_start", "SPACECRAFT_ID"])
    )


def landsat_collection(region: ee.Geometry, year: int, season: Season) -> ee.ImageCollection:
    sensors, _ = sensors_for_year(year)
    start, end = season.date_range(year)
    merged = None
    for s in sensors:
        cid, bands = _SENSORS[s]
        c = (
            ee.ImageCollection(cid)
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUD_COVER", 60))   # scene filter is loose; pixel QA does the real work
            .select(bands, _COMMON)
        )
        merged = c if merged is None else merged.merge(c)
    return merged.map(_prep)


def _empty(bands: list[str]) -> ee.Image:
    return ee.Image.constant([0] * len(bands)).rename(bands).toFloat().updateMask(ee.Image.constant(0))


COMPOSITE_BANDS = ["LST", "LST_sd", "obs", "NDVI", "NDBI", "MNDWI"]


def composite(region: ee.Geometry, year: int, season: Season, with_sr: bool = True) -> ee.Image:
    """Seasonal composite. Returns a masked placeholder if no scenes exist (never a band-less image)."""
    col = landsat_collection(region, year, season)
    lst = col.select("LST")
    img = lst.mean().rename("LST").addBands(lst.reduce(ee.Reducer.stdDev()).rename("LST_sd")).addBands(
        lst.count().rename("obs").toFloat()
    )
    bands = ["LST", "LST_sd", "obs"]
    if with_sr:
        sr = col.select(_SR_BANDS).median()
        ndvi = sr.normalizedDifference(["nir", "red"]).rename("NDVI")
        ndbi = sr.normalizedDifference(["swir1", "nir"]).rename("NDBI")
        mndwi = sr.normalizedDifference(["green", "swir1"]).rename("MNDWI")
        img = img.addBands(ndvi).addBands(ndbi).addBands(mndwi)
        bands = COMPOSITE_BANDS
    return ee.Image(ee.Algorithms.If(col.size().gt(0), img.toFloat(), _empty(bands))).set("year", year)


def collection_meta(region: ee.Geometry, year: int, season: Season) -> ee.Dictionary:
    col = landsat_collection(region, year, season)
    return ee.Dictionary({
        "scene_count": col.size(),
        "sensors": col.aggregate_array("SPACECRAFT_ID").distinct(),
    })


# ─────────────────────────────── ancillary layers ─────────────────────────

def ghsl_epoch(year: int) -> int:
    """Nearest GHSL epoch (5-yearly). 2025+ epochs are model projections, so we cap at 2020."""
    return int(min(max(round(year / 5) * 5, 1975), 2020))


def built_fraction(year: int) -> ee.Image:
    e = ghsl_epoch(year)
    return ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{e}").select("built_surface").divide(10000).rename("built")


def population_density(year: int) -> ee.Image:
    """People per km² (GHS-POP counts per 100 m cell × 100)."""
    e = ghsl_epoch(year)
    pop = ee.Image(f"JRC/GHSL/P2023A/GHS_POP/{e}").select("population_count")
    return pop.updateMask(pop.gte(0)).multiply(100).rename("pop_density")   # nodata = -200


def water_mask() -> ee.Image:
    return ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0).gte(50).rename("water")


def dem() -> ee.Image:
    return ee.ImageCollection("COPERNICUS/DEM/GLO30").select("DEM").mosaic().rename("elev")


def worldcover() -> ee.Image:
    return ee.ImageCollection("ESA/WorldCover/v200").first().select("Map").rename("landcover")


def worldcover_vis_image() -> ee.Image:
    values = [v for v, _, _ in WORLDCOVER]
    idx = list(range(len(values)))
    return worldcover().remap(values, idx)


# ─────────────────────────────── analysis context ─────────────────────────

def utm_epsg(lat: float, lon: float) -> str:
    zone = int(math.floor((lon + 180) / 6) + 1)
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


@dataclass
class AnalysisContext:
    city: str
    display_name: str
    lat: float
    lon: float
    boundary_geojson: dict
    ring_geojson: dict
    map_bounds: list
    boundary_source: str
    area_km2: float
    crs: str
    extra: dict = field(default_factory=dict)

    @property
    def boundary(self) -> ee.Geometry:
        return ee.Geometry(self.boundary_geojson)

    @property
    def ring(self) -> ee.Geometry:
        return ee.Geometry(self.ring_geojson)

    @property
    def study_area(self) -> ee.Geometry:
        """Boundary + rural ring: region used for compositing and map tiles."""
        return ee.Geometry.Rectangle(self.map_bounds, None, False)

    def public(self) -> dict:
        return {
            "city": self.city,
            "display_name": self.display_name,
            "lat": self.lat,
            "lon": self.lon,
            "boundary": self.boundary_geojson,
            "rural_ring": self.ring_geojson,
            "boundary_source": self.boundary_source,
            "map_bounds": self.map_bounds,
            "area_km2": round(self.area_km2, 1),
        }


MAX_RADIUS_M = 40000  # clip sprawling admin areas (e.g. prefecture-sized cities) to 40 km around the centre


@ttl_cache(ttl=7 * 24 * 3600)
def build_context(city: str) -> AnalysisContext:
    from app.services.geocoding_service import geocode_city

    initialize_gee()
    loc = geocode_city(city.strip())
    raw = ee.Geometry(loc["geometry"])
    centre = ee.Geometry.Point([loc["lon"], loc["lat"]])
    source = loc["boundary_source"]

    min_lon, min_lat, max_lon, max_lat = loc["bbox"]
    span_km = max((max_lon - min_lon) * 111 * math.cos(math.radians(loc["lat"])), (max_lat - min_lat) * 111)
    if span_km > 2 * MAX_RADIUS_M / 1000:
        raw = raw.intersection(centre.buffer(MAX_RADIUS_M), 100)
        source += "_clipped"

    boundary = raw.simplify(50)
    ring = boundary.buffer(RURAL_RING_OUTER_M, 200).difference(boundary.buffer(RURAL_RING_INNER_M, 200), 200).simplify(100)
    info = getinfo(ee.Dictionary({
        "boundary": boundary,
        "ring": ring,
        "bounds": ring.bounds(200).coordinates(),
        "area": boundary.area(100),
        "elev": dem().reduceRegion(ee.Reducer.median(), boundary, 90, maxPixels=1e9, tileScale=4).get("elev"),
    }))

    coords = info["bounds"][0]
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    return AnalysisContext(
        city=loc["name"],
        display_name=loc["display_name"],
        lat=loc["lat"],
        lon=loc["lon"],
        boundary_geojson=info["boundary"],
        ring_geojson=info["ring"],
        map_bounds=[min(xs), min(ys), max(xs), max(ys)],
        boundary_source=source,
        area_km2=info["area"] / 1e6,
        crs=utm_epsg(loc["lat"], loc["lon"]),
        extra={"median_elev": info.get("elev")},
    )


# ─────────────────────────────── masks & helpers ──────────────────────────

def urban_mask(ctx: AnalysisContext, year: int) -> ee.Image:
    """Urban pixels: inside the boundary, GHSL built fraction ≥ 20 %, not water."""
    return (
        built_fraction(year).gte(URBAN_BUILT_THRESHOLD)
        .And(water_mask().Not())
        .clip(ctx.boundary)
        .rename("urban")
    )


def rural_mask(ctx: AnalysisContext, year: int) -> ee.Image:
    """Rural reference: 1–10 km ring, built fraction < 5 %, not water, elevation within ±100 m of the city's median."""
    m = built_fraction(year).lt(RURAL_BUILT_THRESHOLD).And(water_mask().Not())
    city_elev = ctx.extra.get("median_elev")
    if city_elev is not None:
        m = m.And(dem().subtract(city_elev).abs().lte(RURAL_MAX_ELEV_DIFF_M))
    return m.clip(ctx.ring).rename("rural")


def land_mask() -> ee.Image:
    return water_mask().Not()


_RETRYABLE = ("Too many concurrent", "Computation timed out", "429", "503", "Internal error")


def getinfo(obj, retries: int = 4):
    """getInfo() with exponential back-off on Earth Engine concurrency / transient errors."""
    delay = 3
    for attempt in range(retries + 1):
        try:
            return obj.getInfo()
        except ee.EEException as e:
            if attempt == retries or not any(t in str(e) for t in _RETRYABLE):
                raise
            time.sleep(delay)
            delay *= 2


def tile_url(image: ee.Image, vis: dict) -> str:
    return image.getMapId(vis)["tile_fetcher"].url_format


def round_or_none(v, nd=2):
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None
