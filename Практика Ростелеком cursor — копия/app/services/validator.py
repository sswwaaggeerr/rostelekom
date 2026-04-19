from __future__ import annotations

from collections import Counter

import pandas as pd

from app.services.parser import REQUIRED_COLUMNS

ALLOWED_TASK_TYPES = {
    "инсталляция",
    "снятие",
    "обследование",
    "техподдержка",
    "доработка",
}


def validate_dataframe(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        errors.append(f"Отсутствуют обязательные колонки: {', '.join(missing)}")
        return errors

    if df.empty:
        return ["Файл не содержит задач."]

    has_lat = "lat" in df.columns
    has_lon = "lon" in df.columns

    ids = df["task_id"].astype(str).tolist()
    duplicates = [k for k, v in Counter(ids).items() if v > 1]
    if duplicates:
        errors.append(f"Дубли task_id: {', '.join(duplicates[:10])}")

    for index, row in df.iterrows():
        row_num = index + 2
        if pd.isna(row["address"]) or not str(row["address"]).strip():
            errors.append(f"Строка {row_num}: пустой address")

        if has_lat or has_lon:
            lat = row.get("lat") if has_lat else None
            lon = row.get("lon") if has_lon else None
            if (pd.isna(lat) and not pd.isna(lon)) or (not pd.isna(lat) and pd.isna(lon)):
                errors.append(f"Строка {row_num}: если указываете координаты, нужны оба поля lat и lon")
            if not pd.isna(lat) and not pd.isna(lon):
                try:
                    lat_f = float(str(lat).replace(",", "."))
                    lon_f = float(str(lon).replace(",", "."))
                    if not (-90 <= lat_f <= 90) or not (-180 <= lon_f <= 180):
                        errors.append(f"Строка {row_num}: координаты вне диапазона (lat -90..90, lon -180..180)")
                except Exception:
                    errors.append(f"Строка {row_num}: некорректные lat/lon (ожидаются числа)")

        try:
            visit_time = pd.to_datetime(row["visit_time"])
            sla_deadline = pd.to_datetime(row["sla_deadline"])
            if sla_deadline < visit_time:
                errors.append(f"Строка {row_num}: sla_deadline раньше visit_time")
        except Exception:
            errors.append(f"Строка {row_num}: некорректный формат visit_time/sla_deadline")

        try:
            duration = int(row["duration_min"])
            if duration <= 0:
                errors.append(f"Строка {row_num}: duration_min должен быть > 0")
        except Exception:
            errors.append(f"Строка {row_num}: некорректный duration_min")

        task_type = str(row["task_type"]).strip().lower()
        if task_type not in ALLOWED_TASK_TYPES:
            errors.append(f"Строка {row_num}: неизвестный task_type '{task_type}'")

    return errors
