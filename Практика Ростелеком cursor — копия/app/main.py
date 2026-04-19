from __future__ import annotations

from datetime import time
from io import BytesIO

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.schemas import OptimizerSettings
from app.services.optimizer import plan_tasks
from app.services.parser import parse_tasks
from app.services.routing import Point, Router, RouteLeg, now_unix
from app.services.validator import validate_dataframe

app = FastAPI(title="RTK Brigade Planner")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/validate")
async def validate_file(file: UploadFile = File(...)):
    df = _read_excel(file)
    errors = validate_dataframe(df)
    return JSONResponse({"ok": len(errors) == 0, "errors": errors})


@app.post("/api/process")
async def process_file(
    file: UploadFile = File(...),
    brigade_count: int = Form(8),
    duty_brigade_id: str = Form("B1"),
    traffic_level: str = Form("hard"),
    base_travel_hours: float = Form(0.5),
    min_completion_ratio: float = Form(0.82),
    max_overtime_minutes: int = Form(120),
):
    df = _read_excel(file)
    errors = validate_dataframe(df)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    settings = OptimizerSettings(
        brigade_count=brigade_count,
        duty_brigade_id=duty_brigade_id,
        base_travel_hours=base_travel_hours,
        traffic_level=traffic_level,
        task_type_weights={
            "инсталляция": 30,
            "снятие": 20,
            "обследование": 40,
            "техподдержка": 10,
            "доработка": 50,
        },
        min_completion_ratio=min_completion_ratio,
        max_overtime_minutes=max_overtime_minutes,
        work_start=time(8, 0),
        work_end=time(17, 0),
        lunch_start=time(12, 0),
        lunch_end=time(13, 0),
        duty_work_start=time(8, 0),
        duty_work_end=time(20, 0),
        duty_lunch_start=time(14, 0),
        duty_lunch_end=time(15, 0),
        selected_task_types=[
            "инсталляция",
            "снятие",
            "обследование",
            "техподдержка",
            "доработка",
        ],
    )
    tasks = parse_tasks(df)
    result = plan_tasks(tasks, settings)
    result = await _enrich_with_routes(result)
    return JSONResponse({"ok": True, "result": result})


def _read_excel(file: UploadFile) -> pd.DataFrame:
    content = file.file.read()
    file.file.seek(0)
    return pd.read_excel(BytesIO(content))


async def _enrich_with_routes(result: dict) -> dict:
    """
    Adds per-brigade 'route' section with coordinates and OSRM routing.
    Never fails the request: if routing is unavailable, includes a short reason.
    """
    router = Router()

    for brigade in result.get("brigades", []):
        tasks = brigade.get("tasks") or []
        points: list[Point] = []
        for t in tasks:
            lat = t.get("lat")
            lon = t.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                points.append(Point(lat=float(lat), lon=float(lon)))

        if len(points) < 2:
            brigade["route"] = {
                "enabled": False,
                "reason": "Для маршрута нужны координаты lat/lon минимум у 2 задач." if tasks else "Нет задач.",
            }
            continue

        legs: list[dict] = []
        total_distance_km = 0.0
        total_duration_min = 0.0
        any_times = False

        provider = router.default_provider()
        departure = now_unix()
        for idx in range(len(points) - 1):
            a = points[idx]
            b = points[idx + 1]
            dist_km, dur_min, provider = await router.route_leg(a, b, departure_unix=None, provider=provider)
            if dist_km is not None:
                total_distance_km += dist_km
            if dur_min is not None:
                total_duration_min += dur_min
                any_times = True
                departure += int(dur_min * 60)

            from_id = tasks[idx].get("task_id")
            to_id = tasks[idx + 1].get("task_id")
            legs.append(
                {
                    "from_task_id": from_id,
                    "to_task_id": to_id,
                    "distance_km": round(dist_km, 2) if dist_km is not None else None,
                    "duration_min": round(dur_min, 1) if dur_min is not None else None,
                    "provider": provider,
                }
            )

        route_provider = provider
        note = None
        if route_provider == "osrm":
            note = "ORS не настроен — используется публичный OSRM без учета пробок."
        elif route_provider == "yandex":
            note = "Используется Яндекс.Маршруты с пробками."

        brigade["route"] = {
            "enabled": any_times,
            "provider": route_provider,
            "points": [[p.lat, p.lon] for p in points],  # For frontend map rendering
            "total_distance_km": round(total_distance_km, 2) if total_distance_km else None,
            "total_duration_min": round(total_duration_min, 1) if any_times else None,
            "legs": legs,
            "note": note,
        }

    return result
