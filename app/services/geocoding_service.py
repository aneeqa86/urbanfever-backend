import httpx

async def geocode_city(city: str) -> dict:
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": city,
        "format": "json",
        "limit": 1,
        "accept-language": "en"
    }
    headers = {"User-Agent": "UrbanFever/1.0"}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=headers)
        data = response.json()
    if not data:
        raise ValueError(f"City '{city}' not found")
    result = data[0]
    bbox = result.get("boundingbox")
    name = result["display_name"].split(",")[0].strip()
    return {
        "name": name,
        "lat": float(result["lat"]),
        "lon": float(result["lon"]),
        "bbox": [
            float(bbox[2]),
            float(bbox[0]),
            float(bbox[3]),
            float(bbox[1])
        ]
    }