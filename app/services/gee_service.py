import ee
from app.core.config import settings

_initialized = False

def initialize_gee():
    global _initialized
    if _initialized:
        return
    credentials = ee.ServiceAccountCredentials(
        settings.GEE_SERVICE_ACCOUNT,
        settings.GEE_KEY_FILE
    )
    ee.Initialize(credentials, project=settings.GEE_PROJECT_ID)
    _initialized = True


def _get_mission_config(year: int) -> dict:
    """Return the correct Landsat collection and thermal band for a given year."""
    if year >= 2013:
        return {
            'collection': 'LANDSAT/LC08/C02/T1_L2',
            'thermal_band': 'ST_B10',
        }
    elif year >= 1999:
        return {
            'collection': 'LANDSAT/LE07/C02/T1_L2',
            'thermal_band': 'ST_B6',
        }
    elif year >= 1984:
        return {
            'collection': 'LANDSAT/LT05/C02/T1_L2',
            'thermal_band': 'ST_B6',
        }
    else:
        raise ValueError("No Landsat data available before 1984.")


def _expand_bbox(bbox: list, factor: float = 0.6) -> list:
    """
    Expand a bbox [minLon, minLat, maxLon, maxLat] outward by `factor`
    of its own width/height on each side.

    factor=0.6 roughly doubles the visible area, giving the map enough
    satellite coverage to fill the viewport without zooming in too tight.
    Coordinates are clamped to valid lat/lon ranges.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lon_delta = (max_lon - min_lon) * factor
    lat_delta = (max_lat - min_lat) * factor
    return [
        max(min_lon - lon_delta, -180),
        max(min_lat - lat_delta, -90),
        min(max_lon + lon_delta, 180),
        min(max_lat + lat_delta, 90),
    ]


def get_lst_tile_url(bbox: list, year: int) -> dict:
    # Use the original city bbox for mean_lst computation (stays accurate)
    city_region = ee.Geometry.BBox(bbox[0], bbox[1], bbox[2], bbox[3])

    # Use a larger region for tile rendering so tiles fill the map viewport
    expanded = _expand_bbox(bbox, factor=0.6)
    tile_region = ee.Geometry.BBox(
        expanded[0], expanded[1], expanded[2], expanded[3]
    )

    config = _get_mission_config(year)

    def mask_clouds(image):
        qa = image.select('QA_PIXEL')
        cloud_mask = (
            qa.bitwiseAnd(1 << 3).eq(0)
            .And(qa.bitwiseAnd(1 << 5).eq(0))
        )
        return image.updateMask(cloud_mask)

    def compute_lst(image):
        lst = (
            image.select(config['thermal_band'])
            .multiply(0.00341802)
            .add(149.0)
            .subtract(273.15)
        )
        return image.addBands(lst.rename('LST'))

    collection = (
        ee.ImageCollection(config['collection'])
        .filterBounds(tile_region)          # fetch tiles for the expanded region
        .filterDate(f'{year}-05-01', f'{year}-09-30')
        .filter(ee.Filter.lt('CLOUD_COVER', 20))
        .map(mask_clouds)
        .map(compute_lst)
    )

    # Clip tiles to expanded region so colours fill the viewport
    lst_mean = collection.select('LST').mean().clip(tile_region)

    vis_params = {
        'min': 20,
        'max': 50,
        'palette': [
            '040274', '040281', '0502a3', '0502b8', '0502ce',
            '0602e8', '0c03e4', '1001e3', '1601dd', '1e01d9',
            '3201d2', '4101cd', '4f01c9', '5f01c4', '6e01be',
            '7e01b9', '8e01b4', '9e01af', 'ae01a9', 'be01a4',
            'ce019e', 'de0198', 'ee0192', 'f7078a', 'f90e82',
            'f9167a', 'f91d72', 'f9246a', 'f92b62', 'f9325a',
            'f93952', 'f9404a', 'f94742', 'f94e3a', 'f95532',
            'f95c2a', 'f96322', 'f96a1a', 'f97112', 'f9780a',
            'f97f02', 'f98700', 'f98e00', 'f99500', 'f99c00',
            'f9a300', 'f9aa00', 'f9b100', 'f9b800', 'f9bf00',
        ]
    }

    map_id = lst_mean.getMapId(vis_params)
    tile_url = map_id['tile_fetcher'].url_format

    # Mean LST computed over the original city bbox only — stays accurate
    mean_val = lst_mean.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=city_region,
        scale=1000,
        maxPixels=1e9
    ).get('LST').getInfo()

    return {
        'tile_url': tile_url,
        'mean_lst': round(mean_val, 2) if mean_val else None
    }