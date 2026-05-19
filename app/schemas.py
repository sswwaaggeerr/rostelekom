from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional


@dataclass
class TaskItem:
    task_id: str
    address: str
    formatted_address: str
    visit_time: datetime
    visit_end: Optional[datetime]
    sla_deadline: datetime
    task_type: str
    client_type: str
    duration_min: int
    lat: Optional[float] = None
    lon: Optional[float] = None
    status: str = "scheduled"  # scheduled, assigned, completed, postponed
    cluster_id: Optional[str] = None  # Для группировки по территории
    geocode_status: Optional[str] = None


@dataclass
class BrigadeSchedule:
    brigade_id: str
    is_duty: bool
    shift_start: time
    shift_end: time
    lunch_start: time
    lunch_end: time


@dataclass
class OptimizerSettings:
    brigade_count: int
    duty_brigade_id: str
    base_travel_hours: float
    traffic_level: str
    task_type_weights: Dict[str, int]
    min_completion_ratio: float
    max_overtime_minutes: int
    work_start: time
    work_end: time
    lunch_start: time
    lunch_end: time
    duty_work_start: time
    duty_work_end: time
    duty_lunch_start: time
    duty_lunch_end: time
    selected_task_types: List[str]

    # Новые параметры
    cluster_radius_km: float = 2.0  # Радиус кластеризации в км
    max_tasks_per_cluster: int = 10  # Максимум задач на одну бригаду в кластере
    enable_dynamic_update: bool = True  # Режим динамического обновления
    existing_schedules: Optional[dict] = None  # Существующие расписания при догрузке
    brigade_start_points: Optional[dict] = None  # Текущие стартовые точки бригад
