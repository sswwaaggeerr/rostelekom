from __future__ import annotations

import os
import time as time_mod
from dataclasses import dataclass
from typing import Any, Optional

import httpx


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteLeg:
    from_task_id: str
    to_task_id: str
    distance_km: Optional[float]
    duration_min: Optional[float]
    provider: str


def yandex_maps_route_url(points: list[Point]) -> str:
    # rtext uses "lat,lon~lat,lon~..."
    rtext = "~".join(f"{p.lat},{p.lon}" for p in points)
    return f"https://yandex.ru/maps/?rtext={rtext}&rtt=auto"


class Router:
    """
    Routing via OpenRouteService (ORS) when API key is configured.
    Falls back to public OSRM if ORS is not available.
    """

    def __init__(self, *, timeout_s: float = 10.0):
        self._ors_key = os.getenv("ORS_API_KEY")
        self._yandex_key = os.getenv("YANDEX_ROUTING_API_KEY")
        self._timeout = timeout_s

    def is_enabled(self) -> bool:
        return bool(self._ors_key or self._yandex_key)

    def default_provider(self) -> str:
        if self._ors_key:
            return "ors"
        if self._yandex_key:
            return "yandex"
        return "osrm"

    async def route_leg(self, a: Point, b: Point, *, departure_unix: Optional[int] = None, provider: Optional[str] = None) -> tuple[Optional[float], Optional[float], str]:
        """
        Returns: (distance_km, duration_min, provider)
        """
        if provider is None:
            provider = self.default_provider()

        if provider == "ors":
            return await self._route_leg_ors(a, b)
        if provider == "yandex" and self._yandex_key:
            return await self._route_leg_yandex(a, b, departure_unix)
        return await self._route_leg_osrm(a, b)

    async def _route_leg_ors(self, a: Point, b: Point) -> tuple[Optional[float], Optional[float], str]:
        """
        Uses ORS routing with the API key from ORS_API_KEY.
        """
        if not self._ors_key:
            return None, None, "no_ors_key"

        url = "https://api.openrouteservice.org/v2/directions/driving-car"
        headers = {"Authorization": self._ors_key, "Content-Type": "application/json"}
        body = {"coordinates": [[a.lon, a.lat], [b.lon, b.lat]], "units": "km"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                r = await client.post(url, json=body, headers=headers)
                r.raise_for_status()
                data = r.json()
            except Exception:
                return None, None, "ors_error"

        try:
            segment = data["features"][0]["properties"]["segments"][0]
            distance_km = segment["distance"] / 1000.0
            duration_min = segment["duration"] / 60.0
            return distance_km, duration_min, "ors"
        except (KeyError, IndexError):
            return None, None, "ors_invalid_response"

    async def _route_leg_osrm(self, a: Point, b: Point) -> tuple[Optional[float], Optional[float], str]:
        """
        Uses public OSRM server if ORS/Yandex are unavailable.
        """
        url = f"https://router.project-osrm.org/route/v1/driving/{a.lon},{a.lat};{b.lon},{b.lat}"
        params = {"overview": "false", "steps": "false", "annotations": "false"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception:
                return None, None, "osrm_error"

        if data.get("code") != "Ok" or not data.get("routes"):
            return None, None, "osrm_no_route"

        route = data["routes"][0]
        distance_km = route["distance"] / 1000.0
        duration_min = route["duration"] / 60.0
        return distance_km, duration_min, "osrm"

    async def _route_leg_yandex(self, a: Point, b: Point, departure_unix: Optional[int] = None) -> tuple[Optional[float], Optional[float], str]:
        """
        Yandex routing with traffic awareness.
        """
        params: dict[str, Any] = {
            "apikey": self._yandex_key,
            "mode": "driving",
            "traffic": "enabled",
            "waypoints": f"{a.lat},{a.lon}|{b.lat},{b.lon}",
            "results": 1,
        }
        if departure_unix is not None:
            params["departure_time"] = int(departure_unix)

        url = "https://api.routing.yandex.net/v2/route"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()

        # Be permissive: Yandex response shape may vary by plan/levels.
        route0 = None
        if isinstance(data, dict):
            routes = data.get("routes")
            if isinstance(routes, list) and routes:
                route0 = routes[0]

        distance_m = _dig_first_number(route0, ["distance", "value"]) or _dig_first_number(route0, ["distance"])
        duration_s = _dig_first_number(route0, ["duration", "value"]) or _dig_first_number(route0, ["duration"])
        distance_km = (float(distance_m) / 1000.0) if distance_m is not None else None
        duration_min = (float(duration_s) / 60.0) if duration_s is not None else None
        return distance_km, duration_min, "yandex"



def _dig_first_number(obj: Any, path: list[str]) -> Optional[float]:
    """
    Walk dict by keys in path. Accepts either:
    - {"duration": 123}
    - {"duration": {"value": 123}}
    Returns float or None.
    """
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if isinstance(cur, (int, float)):
        return float(cur)
    return None


def now_unix() -> int:
    return int(time_mod.time())

