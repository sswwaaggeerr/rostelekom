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
    rtext = "~".join(f"{p.lat},{p.lon}" for p in points)
    return f"https://yandex.ru/maps/?rtext={rtext}&rtt=auto"


def _decode_polyline(encoded: str) -> list[list[float]]:
    """
    Декодирует Google Encoded Polyline (используется в OSRM overview=full).
    Возвращает список [lat, lon].
    """
    result = []
    index = 0
    lat = 0
    lng = 0

    while index < len(encoded):
        shift = 0
        result_val = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result_val |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result_val >> 1) if result_val & 1 else result_val >> 1
        lat += dlat

        shift = 0
        result_val = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result_val |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result_val >> 1) if result_val & 1 else result_val >> 1
        lng += dlng

        result.append([lat / 1e5, lng / 1e5])

    return result


class Router:
    """
    Маршрутизация через OSRM (бесплатно, без ключей).
    Опционально — OpenRouteService (ORS_API_KEY) или Яндекс (YANDEX_ROUTING_API_KEY).

    ВАЖНО: теперь возвращает ГЕОМЕТРИЮ маршрута (список координат по дорогам),
    а не просто прямую линию от точки А к точке Б.
    """

    def __init__(self, *, timeout_s: float = 15.0):
        self._ors_key = os.getenv("ORS_API_KEY")
        self._yandex_key = os.getenv("YANDEX_ROUTING_API_KEY")
        self._timeout = timeout_s

    def default_provider(self) -> str:
        if self._ors_key:
            return "ors"
        if self._yandex_key:
            return "yandex"
        return "osrm"

    async def route_leg(
        self,
        a: Point,
        b: Point,
        *,
        departure_unix: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[float], str, list[list[float]]]:
        """
        Возвращает: (distance_km, duration_min, provider, geometry)
        geometry — список точек [[lat, lon], ...] по реальным дорогам.
        """
        if provider is None:
            provider = self.default_provider()

        if provider == "ors" and self._ors_key:
            return await self._route_osrm(a, b)  # Fallback to OSRM even for ORS key for geometry
        if provider == "yandex" and self._yandex_key:
            dist, dur, prov = await self._route_yandex(a, b, departure_unix)
            # Яндекс не отдаёт геометрию в базовом API, используем OSRM для линии
            _, _, _, geom = await self._route_osrm(a, b)
            return dist, dur, prov, geom
        return await self._route_osrm(a, b)

    async def _route_osrm(self, a: Point, b: Point) -> tuple[Optional[float], Optional[float], str, list[list[float]]]:
        """
        OSRM — бесплатный, без ключей.
        overview=full&geometries=polyline — возвращает полную геометрию маршрута.
        """
        url = (
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{a.lon},{a.lat};{b.lon},{b.lat}"
        )
        params = {
            "overview": "full",          # Полная геометрия маршрута
            "geometries": "polyline",    # Encoded Polyline формат
            "steps": "false",
            "annotations": "false",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"   ⚠️ OSRM error: {e}")
                # Fallback: прямая линия
                return None, None, "osrm_error", [[a.lat, a.lon], [b.lat, b.lon]]

        if data.get("code") != "Ok" or not data.get("routes"):
            return None, None, "osrm_no_route", [[a.lat, a.lon], [b.lat, b.lon]]

        route = data["routes"][0]
        distance_km = route["distance"] / 1000.0
        duration_min = route["duration"] / 60.0

        # Декодируем геометрию маршрута (реальная дорога!)
        geometry_encoded = route.get("geometry", "")
        if geometry_encoded:
            try:
                geometry = _decode_polyline(geometry_encoded)
            except Exception:
                geometry = [[a.lat, a.lon], [b.lat, b.lon]]
        else:
            geometry = [[a.lat, a.lon], [b.lat, b.lon]]

        return distance_km, duration_min, "osrm", geometry

    async def _route_yandex(
        self, a: Point, b: Point, departure_unix: Optional[int] = None
    ) -> tuple[Optional[float], Optional[float], str]:
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
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception:
                return None, None, "yandex_error"

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