from fastapi.testclient import TestClient

from app.main import app
from app.services.analysis.trend import mann_kendall
from app.services.analysis.zones import gwr, hex_grid
from app.services.gee_service import resolve_season

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "status" in response.json()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_validation_same_years():
    r = client.get("/api/v1/analysis/overview", params={"city": "Karachi", "year1": 2015, "year2": 2015})
    assert r.status_code == 400


def test_validation_pre_landsat():
    r = client.get("/api/v1/analysis/overview", params={"city": "Karachi", "year1": 1970, "year2": 2015})
    assert r.status_code == 400


def test_season_by_hemisphere():
    assert resolve_season(40, None, None).label == "May–Sep"
    s = resolve_season(-33, None, None)
    assert s.mode == "auto-south" and s.date_range(2020) == ("2019-11-01", "2020-04-01")
    assert resolve_season(5, None, None).mode == "auto-tropical"
    assert resolve_season(5, 3, 5).date_range(2020) == ("2020-03-01", "2020-06-01")


def test_mann_kendall_increasing():
    years = list(range(2000, 2020))
    r = mann_kendall(years, [20 + 0.05 * (y - 2000) for y in years])
    assert r["significant"] and abs(r["sen_slope_per_decade"] - 0.5) < 1e-6 and r["kendall_tau"] == 1.0


def test_mann_kendall_flat_not_significant():
    years = list(range(2000, 2012))
    r = mann_kendall(years, [20, 21, 20, 21, 20, 21, 20, 21, 20, 21, 20, 21])
    assert not r["significant"]


def test_hex_grid_covers_square():
    sq = {"type": "Polygon", "coordinates": [[[0, 0], [0.2, 0], [0.2, 0.2], [0, 0.2], [0, 0]]]}
    hexes, side = hex_grid(sq, 0.1, 0.1, 490)
    assert 150 < len(hexes) < 400 and side > 0


def test_gwr_recovers_coefficients():
    import numpy as np

    rng = np.random.default_rng(0)
    xy = rng.uniform(0, 10, (80, 2))
    x1 = rng.uniform(0, 1, 80)
    y = 40 - 8 * x1
    X = np.column_stack([np.ones(80), x1])
    betas, r2 = gwr(xy, y, X, k=20)
    assert np.allclose(betas[:, 1], -8, atol=1e-6) and np.nanmin(r2) > 0.999
