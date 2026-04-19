from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from app.schemas import BrigadeSchedule, OptimizerSettings, TaskItem

TRAFFIC_MAP = {"free": 0.0, "hard": 0.5, "jam": 1.0}


def build_brigades(settings: OptimizerSettings) -> list[BrigadeSchedule]:
    brigades: list[BrigadeSchedule] = []
    for i in range(1, settings.brigade_count + 1):
        brigade_id = f"B{i}"
        is_duty = brigade_id == settings.duty_brigade_id
        if is_duty:
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


def plan_tasks(tasks: list[TaskItem], settings: OptimizerSettings) -> dict:
    brigades = build_brigades(settings)
    sorted_tasks = sorted(tasks, key=lambda t: _priority_key(t, settings))
    agenda = {b.brigade_id: [] for b in brigades}
    clocks = {b.brigade_id: _start_dt(sorted_tasks, b) for b in brigades}

    completed = 0
    for task in sorted_tasks:
        best = _choose_brigade(task, brigades, clocks, settings)
        if not best:
            continue
        brigade_id, start_at, finish_at, sla_status = best
        agenda[brigade_id].append(
            {
                "task_id": task.task_id,
                "address": task.address,
                "lat": task.lat,
                "lon": task.lon,
                "task_type": task.task_type,
                "client_type": task.client_type,
                "planned_start": start_at.isoformat(sep=" ", timespec="minutes"),
                "planned_finish": finish_at.isoformat(sep=" ", timespec="minutes"),
                "sla_deadline": task.sla_deadline.isoformat(sep=" ", timespec="minutes"),
                "sla_status": sla_status,
            }
        )
        clocks[brigade_id] = finish_at
        completed += 1

    completion_ratio = (completed / len(tasks) * 100.0) if tasks else 0.0
    return {
        "completion_ratio": round(completion_ratio, 2),
        "target_ratio": round(settings.min_completion_ratio * 100, 2),
        "target_achieved": completion_ratio >= settings.min_completion_ratio * 100,
        "brigades": [{"brigade_id": b.brigade_id, "is_duty": b.is_duty, "tasks": agenda[b.brigade_id]} for b in brigades],
        "meta": _serialize_settings(settings),
    }


def _priority_key(task: TaskItem, settings: OptimizerSettings) -> tuple:
    type_weight = settings.task_type_weights.get(task.task_type, 50)
    return (task.sla_deadline, type_weight, task.visit_time)


def _choose_brigade(task: TaskItem, brigades: list[BrigadeSchedule], clocks: dict[str, datetime], settings: OptimizerSettings):
    travel_delta = timedelta(hours=settings.base_travel_hours + TRAFFIC_MAP.get(settings.traffic_level, 0.5))
    best = None
    for brigade in brigades:
        start_at = max(clocks[brigade.brigade_id], task.visit_time) + travel_delta
        start_at = _adjust_for_lunch(start_at, brigade)
        finish_at = start_at + timedelta(minutes=task.duration_min)
        if not _is_within_shift_or_overtime(finish_at, brigade, settings.max_overtime_minutes):
            continue
        sla_status = "ok" if finish_at <= task.sla_deadline else "breach"
        candidate = (brigade.brigade_id, start_at, finish_at, sla_status)
        if best is None or finish_at < best[2]:
            best = candidate
    return best


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
