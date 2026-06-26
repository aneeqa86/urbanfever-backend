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

def get_lst_tile_url(bbox: list, year: int) -> dict:
    region = ee.Geometry.BBox(bbox[0], bbox[1], bbox[2], bbox[3])

    def mask_clouds(image):
        qa = image.select('QA_PIXEL')
        cloud_mask = qa.bitwiseAnd(1 << 3).eq(0).And(qa.bitwiseAnd(1 << 5).eq(0))
        return image.updateMask(cloud_mask)

    def compute_lst(image):
        lst = image.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15)
        return image.addBands(lst.rename('LST'))

    collection = (
        ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
        .filterBounds(region)
        .filterDate(f'{year}-05-01', f'{year}-09-30')
        .filter(ee.Filter.lt('CLOUD_COVER', 20))
        .map(mask_clouds)
        .map(compute_lst)
    )

    lst_mean = collection.select('LST').mean().clip(region)

    vis_params = {
        'min': 20,
        'max': 50,
        'palette': ['040274', '040281', '0502a3', '0502b8', '0502ce',
                    '0602e8', '0c03e4', '1001e3', '1601dd', '1e01d9',
                    '3201d2', '4101cd', '4f01c9', '5f01c4', '6e01be',
                    '7e01b9', '8e01b4', '9e01af', 'ae01a9', 'be01a4',
                    'ce019e', 'de0198', 'ee0192', 'f7078a', 'f90e82',
                    'f9167a', 'f91d72', 'f9246a', 'f92b62', 'f9325a',
                    'f93952', 'f9404a', 'f94742', 'f94e3a', 'f95532',
                    'f95c2a', 'f96322', 'f96a1a', 'f97112', 'f9780a',
                    'f97f02', 'f98700', 'f98e00', 'f99500', 'f99c00',
                    'f9a300', 'f9aa00', 'f9b100', 'f9b800', 'f9bf00']
    }

    map_id = lst_mean.getMapId(vis_params)
    tile_url = map_id['tile_fetcher'].url_format

    mean_val = lst_mean.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=1000,
        maxPixels=1e9
    ).get('LST').getInfo()

    return {
        'tile_url': tile_url,
        'mean_lst': round(mean_val, 2) if mean_val else None
    }
