# UrbanFever API

FastAPI + Google Earth Engine backend for urban heat analysis from Landsat 5/7/8/9 (1984 → present).

## Endpoints (`/api/v1/analysis/…`)

| Endpoint | What it computes |
|---|---|
| `overview` | Landsat composites for two years, SUHI (urban − rural ring), ΔLST, real pixel histograms, data-quality metadata |
| `trend` | Annual seasonal-mean series 1985 → present, Theil–Sen slope + Mann-Kendall (city & per pixel), ERA5-Land validation |
| `hotspots` | Getis-Ord Gi* hot/cold spot classes |
| `drivers` | NDVI / NDBI / MNDWI layers, LST regressions, LST by ESA WorldCover class |
| `landchange` | GHSL built-up change × NDVI change categories vs ΔLST |
| `zones` | Hexagon zonal statistics, Heat Vulnerability Index, geographically weighted regression |
| `cooling` | Distance-decay cooling effect of green space and water |
| `transect` (POST) | LST / NDVI profile along a line |
| `pixel` | All layer values + 40-year series at a point |
| `export` | GeoTIFF download URL (`lst_y1`, `lst_y2`, `delta`, `ndvi`, `hotspots`, `trend_slope`) |

Common query parameters: `city`, `year1`, `year2`, optional `start_month` + `end_month`
(omit for automatic season: May–Sep north of 23.5°N, Nov–Mar south of 23.5°S, full year in the tropics).
Interactive docs: `http://localhost:8000/docs`. `/api/v1/lst/lst` is kept as a deprecated alias.

## Method notes

- Landsat Collection 2 Level-2; QA_PIXEL bits 0–5 masked (fill, dilated cloud, cirrus, cloud, shadow, snow).
- L8 + L9 merged from 2013; L5 + L7 merged 1999–2011 (L7 SLC-off flagged); L7 only in 2012.
- City boundary from OpenStreetMap (Nominatim polygon), distant parts dropped, clipped to 40 km.
- Urban = GHSL built-up ≥ 20 %; rural = 1–10 km ring, built-up < 5 %, not water, ±100 m elevation.
- Statistics computed in the local UTM zone. Results cached in memory for 6 h.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # fill in GEE_PROJECT_ID / service account
uvicorn app.main:app --reload --port 8000
pytest tests/
```

Deployment: see `../docs/DEPLOYMENT.md` (Google Cloud Run, keyless auth via the service identity).
