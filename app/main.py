from __future__ import annotations

from datetime import time
from io import BytesIO
from typing import List

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.schemas import OptimizerSettings
from app.services.optimizer import plan_tasks
from app.services.parser import parse_tasks
from app.services.geocoder import geocode_addresses
from app.services.routing import Point, Router, now_unix
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
    cluster_radius_km: float = Form(2.0),
    status_filter: str = Form("all"),
    save_schedule: bool = Form(False),
    # Новые параметры для пробок — строка JSON вида:
    # [{"name":"Глазковский мост","lat":52.28,"lon":104.28,"delay_min":20}]
    bottlenecks: str = Form("[]"),
    # {"B1":{"address":"Иркутск, Горького, д.25","lat":52.284742,"lon":104.285432}}
    brigade_start_points: str = Form("{}"),
):
    import json

    df = _read_excel(file)
    errors = validate_dataframe(df)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    df = await geocode_addresses(df)

    try:
        bottleneck_list: list[dict] = json.loads(bottlenecks)
    except Exception:
        bottleneck_list = []
    try:
        brigade_start_point_map: dict = json.loads(brigade_start_points)
    except Exception:
        brigade_start_point_map = {}

    settings = OptimizerSettings(
        brigade_count=brigade_count,
        duty_brigade_id=duty_brigade_id,
        base_travel_hours=base_travel_hours,
        traffic_level=traffic_level,
        task_type_weights={
            "инсталляция": 30,
            "снятие": 20,
            "обследование": 40,
            "устранение неисправностей": 10,
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
            "инсталляция", "снятие", "обследование", "устранение неисправностей", "доработка",
        ],
        cluster_radius_km=cluster_radius_km,
        brigade_start_points=brigade_start_point_map,
    )
    tasks = parse_tasks(df)

    if status_filter != "all":
        tasks = [t for t in tasks if t.status == status_filter]

    result = plan_tasks(tasks, settings)

    await _enrich_with_routes_and_coords(result, df, bottleneck_list)
    return JSONResponse({"ok": True, "result": result})


@app.post("/api/append")
async def append_file(
    file: UploadFile = File(...),
    existing_result: str = Form("{}"),
    brigade_count: int = Form(8),
    duty_brigade_id: str = Form("B1"),
    traffic_level: str = Form("hard"),
    base_travel_hours: float = Form(0.5),
    cluster_radius_km: float = Form(2.0),
    bottlenecks: str = Form("[]"),
    brigade_start_points: str = Form("{}"),
):
    """
    Догрузка новых задач к уже существующему расписанию.
    existing_result — JSON предыдущего результата /api/process.
    """
    import json

    df = _read_excel(file)
    errors = validate_dataframe(df)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    df = await geocode_addresses(df)

    try:
        prev = json.loads(existing_result)
    except Exception:
        prev = {}

    try:
        bottleneck_list = json.loads(bottlenecks)
    except Exception:
        bottleneck_list = []
    try:
        brigade_start_point_map = json.loads(brigade_start_points)
    except Exception:
        brigade_start_point_map = {}

    # Строим existing_schedules из предыдущего результата
    existing_schedules = {}
    for brigade in prev.get("brigades", []):
        bid = brigade["brigade_id"]
        tasks_done = brigade.get("tasks", [])
        if tasks_done:
            existing_schedules[bid] = {
                "last_task_finish": tasks_done[-1].get("planned_finish"),
                "task_count": len(tasks_done),
            }

    settings = OptimizerSettings(
        brigade_count=brigade_count,
        duty_brigade_id=duty_brigade_id,
        base_travel_hours=base_travel_hours,
        traffic_level=traffic_level,
        task_type_weights={"инсталляция": 30, "снятие": 20, "обследование": 40, "устранение неисправностей": 10, "доработка": 50},
        min_completion_ratio=0.82,
        max_overtime_minutes=120,
        work_start=time(8, 0),
        work_end=time(17, 0),
        lunch_start=time(12, 0),
        lunch_end=time(13, 0),
        duty_work_start=time(8, 0),
        duty_work_end=time(20, 0),
        duty_lunch_start=time(14, 0),
        duty_lunch_end=time(15, 0),
        selected_task_types=["инсталляция", "снятие", "обследование", "устранение неисправностей", "доработка"],
        cluster_radius_km=cluster_radius_km,
        existing_schedules=existing_schedules,
        brigade_start_points=brigade_start_point_map,
    )

    new_tasks = parse_tasks(df)
    new_result = plan_tasks(new_tasks, settings)

    # Мёрджим: добавляем задачи к существующим бригадам
    merged = _merge_results(prev, new_result)
    await _enrich_with_routes_and_coords(merged, df, bottleneck_list)

    return JSONResponse({"ok": True, "result": merged})


def _merge_results(prev: dict, new: dict) -> dict:
    """Объединяет два результата планирования."""
    if not prev.get("brigades"):
        return new

    merged_brigades = {b["brigade_id"]: b for b in prev.get("brigades", [])}

    for brigade in new.get("brigades", []):
        bid = brigade["brigade_id"]
        if bid in merged_brigades:
            # Добавляем новые задачи к существующим
            merged_brigades[bid]["tasks"].extend(brigade["tasks"])
            if brigade.get("start_point"):
                merged_brigades[bid]["start_point"] = brigade["start_point"]
        else:
            merged_brigades[bid] = brigade

    return {
        **new,
        "brigades": list(merged_brigades.values()),
        "_merged": True,
    }


def _read_excel(file: UploadFile) -> pd.DataFrame:
    content = file.file.read()
    file.file.seek(0)
    return pd.read_excel(BytesIO(content))


async def _enrich_with_routes_and_coords(
    result: dict,
    df: pd.DataFrame,
    bottlenecks: list[dict] = None,
) -> dict:
    """
    1. Добавляет координаты из DataFrame в задачи.
    2. Строит РЕАЛЬНЫЕ маршруты через OSRM (геометрия по дорогам).
    3. Добавляет штраф времени если маршрут проходит близко к «пробочной» точке.
    """
    from app.services.optimizer import haversine_distance  # reuse from optimizer

    bottlenecks = bottlenecks or []

    # Маппинг task_id -> (lat, lon)
    task_coords: dict[str, tuple[float, float]] = {}
    task_id_col = None
    for col in ["Задание", "task_id", "№ Л/С"]:
        if col in df.columns:
            task_id_col = col
            break

    if task_id_col:
        for _, row in df.iterrows():
            tid = str(row.get(task_id_col, "")).strip()
            lat = row.get("lat") if "lat" in df.columns else None
            lon = row.get("lon") if "lon" in df.columns else None
            if tid and lat is not None and lon is not None:
                try:
                    task_coords[tid] = (float(lat), float(lon))
                except Exception:
                    pass

    print(f"📍 Координат для {len(task_coords)} задач")

    router = Router()

    for brigade in result.get("brigades", []):
        tasks = brigade.get("tasks") or []

        # Расставляем координаты
        points: list[Point] = []
        point_task_ids: list[str] = []
        start_point = brigade.get("start_point") or {}
        try:
            if start_point.get("lat") is not None and start_point.get("lon") is not None:
                points.append(Point(lat=float(start_point["lat"]), lon=float(start_point["lon"])))
                point_task_ids.append("START")
        except Exception:
            pass

        for t in tasks:
            tid = t.get("task_id")
            if tid in task_coords:
                t["lat"], t["lon"] = task_coords[tid]
                points.append(Point(lat=task_coords[tid][0], lon=task_coords[tid][1]))
                point_task_ids.append(str(tid))
            elif t.get("lat") is not None and t.get("lon") is not None:
                try:
                    points.append(Point(lat=float(t["lat"]), lon=float(t["lon"])))
                    point_task_ids.append(str(tid))
                except Exception:
                    pass

        if len(points) < 2:
            brigade["route"] = {
                "enabled": False,
                "reason": "Нужна стартовая точка и хотя бы 1 задача с координатами." if tasks else "Нет задач.",
                "points": [],
                "geometry": [],
            }
            continue

        # Строим маршрут по дорогам (OSRM)
        legs: list[dict] = []
        total_distance_km = 0.0
        total_duration_min = 0.0
        full_geometry: list[list[float]] = []  # ← Вся геометрия маршрута бригады

        provider = router.default_provider()
        departure = now_unix()

        for idx in range(len(points) - 1):
            a, b = points[idx], points[idx + 1]

            dist_km, dur_min, prov, geometry = await router.route_leg(
                a, b, departure_unix=departure, provider=provider
            )

            # Добавляем штраф за пробочные точки вдоль этого отрезка
            bottleneck_penalty = _calc_bottleneck_penalty(geometry, bottlenecks)
            if dur_min is not None:
                dur_min += bottleneck_penalty

            if dist_km:
                total_distance_km += dist_km
            if dur_min:
                total_duration_min += dur_min
                departure += int(dur_min * 60)

            # Геометрия: добавляем без дублей первой точки
            if full_geometry and geometry:
                full_geometry.extend(geometry[1:])
            else:
                full_geometry.extend(geometry)

            legs.append({
                "from_task_id": point_task_ids[idx] if idx < len(point_task_ids) else None,
                "to_task_id": point_task_ids[idx + 1] if (idx + 1) < len(point_task_ids) else None,
                "distance_km": round(dist_km, 2) if dist_km else None,
                "duration_min": round(dur_min, 1) if dur_min else None,
                "bottleneck_penalty_min": round(bottleneck_penalty, 1) if bottleneck_penalty else 0,
                "provider": prov,
            })

            provider = prov  # Используем тот же провайдер для всего маршрута

        note = None
        if provider == "osrm":
            note = "OSRM — маршрут по дорогам, без учёта пробок. Используйте настройку пробочных точек."
        elif provider == "yandex":
            note = "Яндекс — с учётом пробок."

        brigade["route"] = {
            "enabled": True,
            "provider": provider,
            "points": [[p.lat, p.lon] for p in points],  # Только waypoints (для маркеров)
            "point_task_ids": point_task_ids,            # task_id для каждого waypoint (чтобы фронт не зависел от индекса)
            "geometry": full_geometry,                     # ← Полная геометрия по дорогам
            "total_distance_km": round(total_distance_km, 2),
            "total_duration_min": round(total_duration_min, 1),
            "legs": legs,
            "note": note,
        }

    return result


def _calc_bottleneck_penalty(geometry: list[list[float]], bottlenecks: list[dict]) -> float:
    """
    Если маршрут (geometry) проходит в радиусе 500м от пробочной точки,
    добавляем delay_min из настроек этой точки.
    Возвращает суммарный штраф в минутах.
    """
    if not bottlenecks or not geometry:
        return 0.0

    penalty = 0.0
    triggered = set()  # Чтобы не учитывать одну пробку дважды на одном сегменте

    for point in geometry:
        plat, plon = point[0], point[1]
        for bn in bottlenecks:
            bn_id = bn.get("name", "")
            if bn_id in triggered:
                continue
            try:
                dist_km = _haversine(plat, plon, float(bn["lat"]), float(bn["lon"]))
                if dist_km <= 0.5:  # 500 метров
                    penalty += float(bn.get("delay_min", 10))
                    triggered.add(bn_id)
            except Exception:
                pass

    return penalty


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import radians, sin, cos, sqrt, atan2
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
