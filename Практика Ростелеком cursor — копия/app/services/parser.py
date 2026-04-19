from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.schemas import TaskItem


REQUIRED_COLUMNS = [
    "task_id",
    "address",
    "visit_time",
    "sla_deadline",
    "task_type",
    "client_type",
    "duration_min",
]


def parse_tasks(df: pd.DataFrame) -> list[TaskItem]:
    tasks: list[TaskItem] = []
    has_lat = "lat" in df.columns
    has_lon = "lon" in df.columns
    for _, row in df.iterrows():
        tasks.append(
            TaskItem(
                task_id=str(row["task_id"]),
                address=str(row["address"]),
                lat=_to_float(row["lat"]) if has_lat else None,
                lon=_to_float(row["lon"]) if has_lon else None,
                visit_time=_to_dt(row["visit_time"]),
                sla_deadline=_to_dt(row["sla_deadline"]),
                task_type=str(row["task_type"]).strip().lower(),
                client_type=str(row["client_type"]),
                duration_min=int(row["duration_min"]),
            )
        )
    return tasks


def _to_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return pd.to_datetime(value).to_pydatetime()


def _to_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    return float(s.replace(",", "."))
