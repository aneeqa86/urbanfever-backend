import httpx

async def geocode_city(city: str) -> dict:
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": city,
        "format": "json",
        "limit": 1
    }
    headers = {"User-Agent": "UrbanFever/1.0"}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=headers)
        data = response.json()
    if not data:
        raise ValueError(f"City '{city}' not found")
    result = data[0]
    bbox = result.get("boundingbox")
    return {
        "name": result["display_name"],
        "lat": float(result["lat"]),
        "lon": float(result["lon"]),
        "bbox": [
            float(bbox[2]),
            float(bbox[0]),
            float(bbox[3]),
            float(bbox[1])
        ]
    }
