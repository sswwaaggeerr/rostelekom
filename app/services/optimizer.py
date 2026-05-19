from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2
from typing import Optional

from app.schemas import BrigadeSchedule, OptimizerSettings, TaskItem

TRAFFIC_MAP = {"free": 0.0, "hard": 0.5, "jam": 1.0}
SLA_WARNING_HOURS = 2
SLA_CRITICAL_HOURS = 1
AVERAGE_CITY_SPEED_KMH = 35.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в км."""
    R = 6371.0
    lat1_rad, lon1_rad = radians(lat1), radians(lon1)
    lat2_rad, lon2_rad = radians(lat2), radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def cluster_tasks_by_location(tasks: list[TaskItem], radius_km: float = 2.0) -> dict[str, list[TaskItem]]:
    """
    Группирует задачи по территориальным кластерам.
    Возвращает dict: cluster_id -> list[TaskItem]
    """
    clusters: dict[str, list[TaskItem]] = {}
    cluster_centers: dict[str, tuple[float, float]] = {}
    tasks_without_coords: list[TaskItem] = []
    
    for task in tasks:
        if task.lat is None or task.lon is None:
            tasks_without_coords.append(task)
            continue
        
        assigned_cluster = None
        for cluster_id, (center_lat, center_lon) in cluster_centers.items():
            dist = haversine_distance(task.lat, task.lon, center_lat, center_lon)
            if dist <= radius_km:
                assigned_cluster = cluster_id
                break
        
        if assigned_cluster:
            task.cluster_id = assigned_cluster
            clusters[assigned_cluster].append(task)
            # Обновляем центр кластера (среднее)
            all_tasks = clusters[assigned_cluster]
            center_lat = sum(t.lat for t in all_tasks) / len(all_tasks)
            center_lon = sum(t.lon for t in all_tasks) / len(all_tasks)
            cluster_centers[assigned_cluster] = (center_lat, center_lon)
        else:
            # Новый кластер
            cluster_id = f"C{len(clusters) + 1}"
            clusters[cluster_id] = [task]
            cluster_centers[cluster_id] = (task.lat, task.lon)
            task.cluster_id = cluster_id
    
    # Задачи без координат помещаем в отдельный кластер
    if tasks_without_coords:
        clusters["NO_COORDS"] = tasks_without_coords
        for task in tasks_without_coords:
            task.cluster_id = "NO_COORDS"
    
    return clusters


def build_brigades(settings: OptimizerSettings, existing_schedules: Optional[dict] = None) -> list[BrigadeSchedule]:
    brigades: list[BrigadeSchedule] = []
    for i in range(1, settings.brigade_count + 1):
        brigade_id = f"B{i}"
        is_duty = brigade_id == settings.duty_brigade_id
        
        # Если есть существующие расписания, используем их
        if existing_schedules and brigade_id in existing_schedules:
            sched = existing_schedules[brigade_id]
            brigades.append(
                BrigadeSchedule(
                    brigade_id=brigade_id,
                    is_duty=is_duty,
                    shift_start=sched.get("shift_start", settings.work_start if not is_duty else settings.duty_work_start),
                    shift_end=sched.get("shift_end", settings.work_end if not is_duty else settings.duty_work_end),
                    lunch_start=sched.get("lunch_start", settings.lunch_start if not is_duty else settings.duty_lunch_start),
                    lunch_end=sched.get("lunch_end", settings.lunch_end if not is_duty else settings.duty_lunch_end),
                )
            )
        elif is_duty:
            brigades.append(
                BrigadeSchedule(
                    brigade_id=brigade_id,
                    is_duty=True,
                    shift_start=settings.duty_work_start,
                    shift_end=settings.duty_work_end,
                    lunch_start=settings.duty_lunch_start,
                    lunch_end=settings.duty_lunch_end,
                )
            )
        else:
            brigades.append(
                BrigadeSchedule(
                    brigade_id=brigade_id,
                    is_duty=False,
                    shift_start=settings.work_start,
                    shift_end=settings.work_end,
                    lunch_start=settings.lunch_start,
                    lunch_end=settings.lunch_end,
                )
            )
    return brigades


def _serialize_settings(settings: OptimizerSettings) -> dict:
    data = asdict(settings)
    for key, value in list(data.items()):
        if isinstance(value, timedelta):
            data[key] = int(value.total_seconds() / 60)
        elif hasattr(value, "isoformat"):
            try:
                data[key] = value.isoformat()
            except TypeError:
                pass
    return data


def _brigade_start_points(settings: OptimizerSettings) -> dict[str, dict]:
    raw = settings.brigade_start_points or {}
    out: dict[str, dict] = {}
    for brigade_id, point in raw.items():
        if not isinstance(point, dict):
            continue
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except Exception:
            continue
        out[str(brigade_id)] = {
            "name": str(point.get("name") or point.get("address") or "Старт"),
            "address": str(point.get("address") or point.get("name") or "Старт"),
            "lat": lat,
            "lon": lon,
        }
    return out


def _coords_from_start_point(point: Optional[dict]) -> Optional[tuple[float, float]]:
    if not point:
        return None
    try:
        return (float(point["lat"]), float(point["lon"]))
    except Exception:
        return None


def plan_tasks(tasks: list[TaskItem], settings: OptimizerSettings) -> dict:
    # Фильтруем задачи по статусу
    active_tasks = [t for t in tasks if t.status in ("scheduled", "assigned")]
    
    brigades = build_brigades(settings, settings.existing_schedules)
    agenda = {b.brigade_id: [] for b in brigades}
    clocks = {b.brigade_id: _start_dt(active_tasks, b) for b in brigades}
    brigade_start_points = _brigade_start_points(settings)
    brigade_positions = {
        b.brigade_id: _coords_from_start_point(brigade_start_points.get(b.brigade_id))
        for b in brigades
    }
    
    # Отслеживаем занятые слоты для предотвращения конфликтов
    occupied_slots: dict = {}
    
    completed = 0
    cluster_assignments: dict[str, str] = {}  # cluster_id -> brigade_id
    cluster_loads = {b.brigade_id: 0 for b in brigades}
    
    # Сравниваем SLA с реальным временем компьютера. Если файл старый,
    # просроченные заявки должны сразу стать красными.
    planning_now = datetime.now()

    clusters = cluster_tasks_by_location(active_tasks, settings.cluster_radius_km)
    ordered_clusters = sorted(
        [(cluster_id, cluster_tasks) for cluster_id, cluster_tasks in clusters.items() if cluster_id != "NO_COORDS"],
        key=lambda item: _cluster_priority_key(item[1], settings),
    )

    # Распределяем заявки географическими группами, чтобы бригады не прыгали через весь город.
    for cluster_id, cluster_tasks in ordered_clusters:
        cluster_brigades_used: set[str] = set()
        preferred_brigade = _find_best_brigade_for_cluster_center(
            cluster_tasks, brigades, clocks, settings, planning_now, brigade_positions, cluster_loads
        )
        ordered_tasks = _order_tasks_within_cluster(
            cluster_tasks,
            brigade_positions.get(preferred_brigade.brigade_id) if preferred_brigade else None,
            settings,
        )

        for task in ordered_tasks:
            assigned = False
            brigade_order = _brigade_order_for_task(
                task,
                brigades,
                preferred_brigade.brigade_id if preferred_brigade else None,
                brigade_positions,
            )

            for brigade in brigade_order:
                result = _assign_task_to_brigade(
                    task, brigade.brigade_id, brigades, clocks, settings, occupied_slots, planning_now, brigade_positions
                )
                if not result:
                    continue
                _append_task_to_agenda(
                    agenda, brigade.brigade_id, task, result, planning_now
                )
                clocks[brigade.brigade_id] = result[1]
                brigade_positions[brigade.brigade_id] = (task.lat, task.lon)
                cluster_assignments.setdefault(cluster_id, brigade.brigade_id)
                cluster_brigades_used.add(brigade.brigade_id)
                completed += 1
                assigned = True
                break

            if not assigned and _is_repair_task(task):
                # Горящий ремонт пробуем отдельно по всем бригадам: SLA важнее красивого кластера.
                fallback = _assign_to_earliest_brigade(
                    task, brigades, clocks, settings, occupied_slots, planning_now, brigade_positions
                )
                if fallback:
                    brigade, result = fallback
                    _append_task_to_agenda(agenda, brigade.brigade_id, task, result, planning_now)
                    clocks[brigade.brigade_id] = result[1]
                    brigade_positions[brigade.brigade_id] = (task.lat, task.lon)
                    cluster_assignments.setdefault(cluster_id, brigade.brigade_id)
                    cluster_brigades_used.add(brigade.brigade_id)
                    completed += 1

        for brigade_id in cluster_brigades_used:
            cluster_loads[brigade_id] += 1
    
    # Находим возможности для проактивных звонков
    proactive_opportunities = _find_proactive_opportunities(tasks, agenda, clocks, settings)
    
    cluster_stats = _compute_cluster_statistics(clusters, cluster_assignments)
    
    completion_ratio = (completed / len(active_tasks) * 100.0) if active_tasks else 0.0
    
    # Выводим статистику (ASCII-safe для Windows консоли)
    print("\n[optimizer] Planning stats:")
    print(f"  total: {len(active_tasks)}")
    print(f"  assigned: {completed}")
    print(f"  postponed: {len(active_tasks) - completed}")
    print(f"  completion: {completion_ratio:.1f}%\n")
    
    return {
        "completion_ratio": round(completion_ratio, 2),
        "target_ratio": round(settings.min_completion_ratio * 100, 2),
        "target_achieved": completion_ratio >= settings.min_completion_ratio * 100,
        "brigades": [
            {
                "brigade_id": b.brigade_id,
                "is_duty": b.is_duty,
                "start_point": brigade_start_points.get(b.brigade_id),
                "tasks": agenda[b.brigade_id],
            }
            for b in brigades
        ],
        "meta": _serialize_settings(settings),
        "clusters": {
            "cluster_stats": cluster_stats,
            "assignments": cluster_assignments,
        },
        "proactive_opportunities": proactive_opportunities,
        "postponed_tasks": _get_postponed_tasks(active_tasks, completed, agenda),
    }


def _priority_key(task: TaskItem, settings: OptimizerSettings) -> tuple:
    """Ключ сортировки: ремонтные SLA первыми, затем тип и интервал визита."""
    type_weight = settings.task_type_weights.get(task.task_type, 50)
    if _is_repair_task(task):
        return (0, task.sla_deadline, type_weight, task.visit_time)
    return (1, task.visit_time, type_weight, task.visit_time)


def _cluster_priority_key(cluster_tasks: list[TaskItem], settings: OptimizerSettings) -> tuple:
    repair_tasks = [t for t in cluster_tasks if _is_repair_task(t)]
    if repair_tasks:
        earliest_sla = min(t.sla_deadline for t in repair_tasks)
        return (0, earliest_sla, -len(cluster_tasks))
    earliest_visit = min(t.visit_time for t in cluster_tasks)
    best_type_weight = min(settings.task_type_weights.get(t.task_type, 50) for t in cluster_tasks)
    return (1, earliest_visit, best_type_weight, -len(cluster_tasks))


def _cluster_center(cluster_tasks: list[TaskItem]) -> Optional[tuple[float, float]]:
    coords = [(t.lat, t.lon) for t in cluster_tasks if t.lat is not None and t.lon is not None]
    if not coords:
        return None
    return (
        sum(lat for lat, _ in coords) / len(coords),
        sum(lon for _, lon in coords) / len(coords),
    )


def _find_best_brigade_for_cluster_center(
    cluster_tasks: list[TaskItem],
    brigades: list[BrigadeSchedule],
    clocks: dict[str, datetime],
    settings: OptimizerSettings,
    planning_now: datetime,
    brigade_positions: dict[str, Optional[tuple[float, float]]],
    cluster_loads: dict[str, int],
) -> Optional[BrigadeSchedule]:
    center = _cluster_center(cluster_tasks)
    best: tuple[float, BrigadeSchedule] | None = None

    for brigade in brigades:
        position = brigade_positions.get(brigade.brigade_id)
        distance = 0.0
        if center and position:
            distance = haversine_distance(position[0], position[1], center[0], center[1])

        available_delay_min = max(0, (clocks[brigade.brigade_id] - planning_now).total_seconds() / 60)
        load_penalty = cluster_loads.get(brigade.brigade_id, 0) * max(settings.cluster_radius_km, 1.0)
        score = distance + available_delay_min / 60 + load_penalty
        if best is None or score < best[0]:
            best = (score, brigade)

    return best[1] if best else None


def _order_tasks_within_cluster(
    cluster_tasks: list[TaskItem],
    start_position: Optional[tuple[float, float]],
    settings: OptimizerSettings,
) -> list[TaskItem]:
    remaining = sorted(cluster_tasks, key=lambda t: _priority_key(t, settings))
    ordered: list[TaskItem] = []
    position = start_position

    while remaining:
        urgent_repairs = [t for t in remaining if _is_repair_task(t)]
        if urgent_repairs:
            next_task = min(urgent_repairs, key=lambda t: t.sla_deadline)
        elif position:
            next_task = min(
                remaining,
                key=lambda t: haversine_distance(position[0], position[1], t.lat, t.lon)
                if t.lat is not None and t.lon is not None else float("inf"),
            )
        else:
            next_task = remaining[0]

        ordered.append(next_task)
        remaining.remove(next_task)
        if next_task.lat is not None and next_task.lon is not None:
            position = (next_task.lat, next_task.lon)

    return ordered


def _brigade_order_for_task(
    task: TaskItem,
    brigades: list[BrigadeSchedule],
    preferred_brigade_id: Optional[str],
    brigade_positions: dict[str, Optional[tuple[float, float]]],
) -> list[BrigadeSchedule]:
    def score(brigade: BrigadeSchedule) -> tuple[int, float]:
        preferred_penalty = 0 if brigade.brigade_id == preferred_brigade_id else 1
        position = brigade_positions.get(brigade.brigade_id)
        distance = 0.0
        if position and task.lat is not None and task.lon is not None:
            distance = haversine_distance(position[0], position[1], task.lat, task.lon)
        return (preferred_penalty, distance)

    return sorted(brigades, key=score)


def _assign_to_earliest_brigade(
    task: TaskItem,
    brigades: list[BrigadeSchedule],
    clocks: dict[str, datetime],
    settings: OptimizerSettings,
    occupied_slots: dict,
    planning_now: datetime,
    brigade_positions: dict[str, Optional[tuple[float, float]]],
) -> Optional[tuple[BrigadeSchedule, tuple[datetime, datetime, str]]]:
    best: tuple[datetime, BrigadeSchedule, tuple[datetime, datetime, str]] | None = None
    for brigade in brigades:
        result = _assign_task_to_brigade(
            task, brigade.brigade_id, brigades, clocks, settings, occupied_slots, planning_now, brigade_positions
        )
        if not result:
            continue
        if best is None or result[1] < best[0]:
            best = (result[1], brigade, result)
    return (best[1], best[2]) if best else None


def _append_task_to_agenda(
    agenda: dict[str, list[dict]],
    brigade_id: str,
    task: TaskItem,
    result: tuple[datetime, datetime, str],
    planning_now: datetime,
) -> None:
    start_at, finish_at, sla_status = result
    sla_deadline = task.sla_deadline if _is_repair_task(task) else None
    agenda[brigade_id].append({
        "task_id": task.task_id,
        "address": task.address,
        "formatted_address": task.formatted_address,
        "lat": task.lat,
        "lon": task.lon,
        "task_type": task.task_type,
        "client_type": task.client_type,
        "visit_window_start": task.visit_time.isoformat(sep=" ", timespec="minutes"),
        "visit_window_end": task.visit_end.isoformat(sep=" ", timespec="minutes") if task.visit_end else None,
        "planned_start": start_at.isoformat(sep=" ", timespec="minutes"),
        "planned_finish": finish_at.isoformat(sep=" ", timespec="minutes"),
        "sla_deadline": sla_deadline.isoformat(sep=" ", timespec="minutes") if sla_deadline else None,
        "sla_status": sla_status,
        "sla_status_text": _sla_status_text(sla_status, task, planning_now),
        "cluster_id": getattr(task, "cluster_id", None),
        "status": task.status,
        "geocode_status": task.geocode_status,
    })


def _find_best_brigade_for_cluster(
    cluster_tasks: list[TaskItem],
    brigades: list[BrigadeSchedule],
    clocks: dict[str, datetime],
    settings: OptimizerSettings,
    occupied_slots: dict,
    cluster_assignments: dict[str, str],
) -> Optional[BrigadeSchedule]:
    """Находит лучшую бригаду для кластера задач."""
    if not cluster_tasks:
        return None
    
    # Проверяем, есть ли координаты у задач
    has_coords = any(t.lat and t.lon for t in cluster_tasks)
    
    if has_coords:
        # Считаем центр кластера
        center_lat = sum(t.lat for t in cluster_tasks if t.lat) / len([t for t in cluster_tasks if t.lat])
        center_lon = sum(t.lon for t in cluster_tasks if t.lon) / len([t for t in cluster_tasks if t.lon])
    else:
        # Нет координат — используем (0, 0) как центр
        center_lat, center_lon = 0.0, 0.0
    
    best_brigade = None
    best_score = float('inf')
    
    for brigade in brigades:
        # Проверяем, не назначена ли уже эта бригада на другой кластер
        assigned_to_other = False
        for cid, bid in cluster_assignments.items():
            if bid == brigade.brigade_id and cid != cluster_tasks[0].cluster_id:
                assigned_to_other = True
                break
        
        if assigned_to_other:
            continue
        
        # Если нет координат, не считаем расстояние (все бригады равны)
        if has_coords:
            current_lat, current_lon = _get_brigade_current_position(brigade.brigade_id, clocks, cluster_tasks)
            dist = haversine_distance(current_lat, current_lon, center_lat, center_lon)
        else:
            dist = 0  # Без координат расстояние не имеет значения
        
        # Считаем сколько задач кластера может выполнить эта бригада
        can_do_count = 0
        total_duration = 0
        now = datetime.now()
        for task in cluster_tasks:
            # Если время визита в прошлом, начинаем сразу от текущего времени бригады
            if task.visit_time < now:
                start_at = clocks[brigade.brigade_id]
            else:
                start_at = max(clocks[brigade.brigade_id], task.visit_time)
            start_at = _adjust_for_lunch(start_at, brigade)
            finish_at = start_at + timedelta(minutes=task.duration_min)
            if _is_within_shift_or_overtime(finish_at, brigade, settings.max_overtime_minutes):
                can_do_count += 1
                total_duration += task.duration_min
        
        # Оценка: чем ближе и чем больше задач может сделать, тем лучше
        score = dist - (can_do_count * 0.5)  # Бонус за количество задач
        
        if score < best_score:
            best_score = score
            best_brigade = brigade
    
    return best_brigade


def _get_brigade_current_position(brigade_id: str, clocks: dict, reference_tasks: list[TaskItem]) -> tuple[float, float]:
    """Определяет текущую позицию бригады."""
    # Пока используем центр reference_tasks как точку отсчета
    if reference_tasks:
        lat = reference_tasks[0].lat or 0.0
        lon = reference_tasks[0].lon or 0.0
        return (lat, lon)
    return (0.0, 0.0)


def _assign_task_to_brigade(
    task: TaskItem,
    brigade_id: str,
    brigades: list[BrigadeSchedule],
    clocks: dict[str, datetime],
    settings: OptimizerSettings,
    occupied_slots: dict,
    planning_now: datetime,
    brigade_positions: dict[str, Optional[tuple[float, float]]],
) -> Optional[tuple[datetime, datetime, str]]:
    """Пытается назначить задачу на бригаду с проверкой конфликтов."""
    brigade = next((b for b in brigades if b.brigade_id == brigade_id), None)
    if not brigade:
        return None
    if task.lat is None or task.lon is None:
        return None
    
    travel_delta = _estimate_travel_delta(task, brigade_positions.get(brigade_id), settings)

    # Планируем от реального текущего времени. Старые выгрузки не должны
    # выглядеть актуальными и зелёными.
    effective_visit = task.visit_time if task.visit_time >= planning_now else planning_now
    start_at = max(clocks[brigade_id], effective_visit) + travel_delta
    
    start_at = _adjust_for_lunch(start_at, brigade)
    finish_at = start_at + timedelta(minutes=task.duration_min)
    
    if not _is_within_shift_or_overtime(finish_at, brigade, settings.max_overtime_minutes):
        return None
    
    sla_status = _calc_sla_status(task, finish_at, planning_now)
    return (start_at, finish_at, sla_status)


def _estimate_travel_delta(
    task: TaskItem,
    current_position: Optional[tuple[float, float]],
    settings: OptimizerSettings,
) -> timedelta:
    traffic_hours = TRAFFIC_MAP.get(settings.traffic_level, 0.5)
    base_hours = settings.base_travel_hours

    if current_position and task.lat is not None and task.lon is not None:
        dist_km = haversine_distance(current_position[0], current_position[1], task.lat, task.lon)
        distance_hours = dist_km / AVERAGE_CITY_SPEED_KMH
        return timedelta(hours=max(base_hours, distance_hours) + traffic_hours)

    return timedelta(hours=base_hours + traffic_hours)


def _is_within_shift_or_overtime(finish_at: datetime, brigade: BrigadeSchedule, max_overtime_minutes: int) -> bool:
    shift_end_dt = finish_at.replace(hour=brigade.shift_end.hour, minute=brigade.shift_end.minute, second=0, microsecond=0)
    overtime_limit = shift_end_dt + timedelta(minutes=max_overtime_minutes)
    return finish_at <= overtime_limit


def _adjust_for_lunch(candidate: datetime, brigade: BrigadeSchedule) -> datetime:
    lunch_start = candidate.replace(hour=brigade.lunch_start.hour, minute=brigade.lunch_start.minute, second=0, microsecond=0)
    lunch_end = candidate.replace(hour=brigade.lunch_end.hour, minute=brigade.lunch_end.minute, second=0, microsecond=0)
    if lunch_start <= candidate < lunch_end:
        return lunch_end
    return candidate


def _start_dt(tasks: list[TaskItem], brigade: BrigadeSchedule) -> datetime:
    base_day = (tasks[0].visit_time if tasks else datetime.now()).date()
    return datetime.combine(base_day, brigade.shift_start)


def _find_proactive_opportunities(all_tasks: list[TaskItem], agenda: dict, clocks: dict, settings: OptimizerSettings) -> list[dict]:
    """
    Находит задачи на будущее время, которые находятся близко к текущим позициям бригад.
    Возвращает список возможностей для проактивных звонков.
    """
    opportunities = []
    
    # Для каждой бригады
    for brigade_id, brigade_tasks in agenda.items():
        if not brigade_tasks:
            continue
        
        # Получаем последнюю позицию бригады
        if not brigade_tasks:
            continue
        
        last_lat = brigade_tasks[-1].get("lat")
        last_lon = brigade_tasks[-1].get("lon")
        if last_lat is None or last_lon is None:
            continue
        
        last_finish = datetime.fromisoformat(brigade_tasks[-1]["planned_finish"])
        
        # Ищем будущие задачи рядом
        for task in all_tasks:
            if task.status != "scheduled":  # Только запланированные на будущее
                continue
            if task.lat is None or task.lon is None:
                continue
            
            dist = haversine_distance(last_lat, last_lon, task.lat, task.lon)
            if dist <= settings.cluster_radius_km and task.visit_time > last_finish:
                time_diff = (task.visit_time - last_finish).total_seconds() / 60
                if 30 <= time_diff <= 480:  # От 30 мин до 8 часов
                    opportunities.append({
                        "brigade_id": brigade_id,
                        "future_task_id": task.task_id,
                        "address": task.address,
                        "scheduled_time": task.visit_time.isoformat(),
                        "distance_km": round(dist, 2),
                        "free_time_before_visit_min": int(time_diff),
                        "client_type": task.client_type,
                    })
    
    return opportunities


def _compute_cluster_statistics(clusters: dict[str, list[TaskItem]], cluster_assignments: dict[str, str]) -> list[dict]:
    """Вычисляет статистику по кластерам (загруженность районов)."""
    stats = []
    for cluster_id, cluster_tasks in clusters.items():
        assigned_brigade = cluster_assignments.get(cluster_id, "Не назначено")
        stats.append({
            "cluster_id": cluster_id,
            "task_count": len(cluster_tasks),
            "assigned_brigade": assigned_brigade,
            "center_lat": sum(t.lat for t in cluster_tasks if t.lat) / len([t for t in cluster_tasks if t.lat]) if any(t.lat for t in cluster_tasks) else None,
            "center_lon": sum(t.lon for t in cluster_tasks if t.lon) / len([t for t in cluster_tasks if t.lon]) if any(t.lon for t in cluster_tasks) else None,
            "sla_breach_count": sum(1 for t in cluster_tasks if _is_repair_task(t) and t.sla_deadline < datetime.now()),
        })
    return stats


def _get_postponed_tasks(all_tasks: list[TaskItem], completed: int, agenda: dict) -> list[dict]:
    """Возвращает список задач, которые не были назначены (потенциально отложенные)."""
    assigned_ids = set()
    for brigade_tasks in agenda.values():
        for t in brigade_tasks:
            assigned_ids.add(t["task_id"])
    
    postponed = []
    for task in all_tasks:
        if task.task_id not in assigned_ids:
            postponed.append({
                "task_id": task.task_id,
                "address": task.address,
                "formatted_address": task.formatted_address,
                "task_type": task.task_type,
                "sla_deadline": task.sla_deadline.isoformat() if _is_repair_task(task) else None,
                "visit_window_start": task.visit_time.isoformat(sep=" ", timespec="minutes"),
                "visit_window_end": task.visit_end.isoformat(sep=" ", timespec="minutes") if task.visit_end else None,
                "reason": _postponed_reason(task),
            })
    return postponed


def _is_repair_task(task: TaskItem) -> bool:
    return (task.task_type or "").strip().lower() == "устранение неисправностей"


def _calc_sla_status(task: TaskItem, finish_at: datetime, planning_now: datetime) -> str:
    if not _is_repair_task(task):
        return "none"
    if planning_now >= task.sla_deadline or finish_at > task.sla_deadline:
        return "breach"

    hours_left = (task.sla_deadline - planning_now).total_seconds() / 3600
    if hours_left <= SLA_CRITICAL_HOURS:
        return "critical"
    if hours_left <= SLA_WARNING_HOURS:
        return "warning"
    return "ok"


def _sla_status_text(status: str, task: TaskItem, planning_now: datetime) -> str:
    if status == "none":
        return "SLA не применяется"
    if status == "breach":
        return "SLA нарушен"

    minutes_left = max(0, int((task.sla_deadline - planning_now).total_seconds() // 60))
    hours = minutes_left // 60
    minutes = minutes_left % 60
    left_text = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"

    if status == "critical":
        return f"Срочно: до SLA {left_text}"
    if status == "warning":
        return f"Внимание: до SLA {left_text}"
    return f"OK: до SLA {left_text}"


def _postponed_reason(task: TaskItem) -> str:
    if task.lat is None or task.lon is None or task.geocode_status == "not_found":
        return "Не вошли в расписание (не смог расшифровать адрес)"
    if _is_repair_task(task) and task.sla_deadline <= datetime.now():
        return "Не вошли в расписание (SLA уже нарушен)"
    return "Не вошли в расписание (не хватило рабочего времени)"
