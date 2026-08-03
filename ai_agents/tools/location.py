import json
from typing import Any
from urllib.request import Request, urlopen

from langchain.tools import tool


LOCATION_SERVICES = {
    "ipwho.is": "https://ipwho.is/",
    "ipapi.co": "https://ipapi.co/json/",
}


def _fetch_location(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "ai-agents/0.1"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def _normalize_location(data: dict[str, Any]) -> dict[str, Any]:
    timezone = data.get("timezone")
    connection = data.get("connection")

    return {
        "ip": data.get("ip"),
        "country": data.get("country") or data.get("country_name"),
        "region": data.get("region"),
        "city": data.get("city"),
        "postal": data.get("postal"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": (
            timezone.get("id") if isinstance(timezone, dict) else timezone
        ),
        "organization": (
            connection.get("org")
            if isinstance(connection, dict)
            else data.get("org")
        ),
    }


@tool
def get_ip_location() -> str:
    """通过公网 IP 获取大致位置；结果仅精确到国家、地区或城市，不是 GPS 定位。"""
    locations: dict[str, Any] = {}

    for name, url in LOCATION_SERVICES.items():
        try:
            locations[name] = _normalize_location(_fetch_location(url))
        except Exception as error:
            locations[name] = {"error": f"{type(error).__name__}: {error}"}

    return json.dumps(locations, ensure_ascii=False, indent=2)
