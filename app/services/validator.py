from __future__ import annotations

from collections import Counter

import pandas as pd

from app.services.parser import COLUMN_MAPPING, _find_column, _map_task_type, _map_status

ALLOWED_TASK_TYPES = {
    "инсталляция",
    "снятие",
    "обследование",
    "устранение неисправностей",
    "доработка",
}


def validate_dataframe(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    
    # Находим колонки с учётом русских названий
    col_task_id = _find_column(df, "task_id")
    col_address = _find_column(df, "address")
    col_visit_time = _find_column(df, "visit_time")
    col_sla_deadline = _find_column(df, "sla_deadline")
    col_task_type = _find_column(df, "task_type")
    col_client_type = _find_column(df, "client_type")
    col_duration_min = _find_column(df, "duration_min")
    col_lat = _find_column(df, "lat")
    col_lon = _find_column(df, "lon")
    col_status = _find_column(df, "status")
    
    # Проверяем обязательные колонки
    required = {
        "task_id": col_task_id,
        "address": col_address,
        "visit_time": col_visit_time,
        "sla_deadline": col_sla_deadline,
        "task_type": col_task_type,
        "client_type": col_client_type,
    }
    
    missing = [k for k, v in required.items() if v is None]
    if missing:
        errors.append(f"Отсутствуют обязательные колонки: {', '.join(missing)}")
        return errors

    if df.empty:
        return ["Файл не содержит задач."]

    has_lat = col_lat is not None
    has_lon = col_lon is not None
    has_status = col_status is not None

    ids = df[col_task_id].astype(str).tolist()
    duplicates = [k for k, v in Counter(ids).items() if v > 1]
    if duplicates:
        errors.append(f"Дубли task_id: {', '.join(duplicates[:10])}")

    for index, row in df.iterrows():
        row_num = index + 2
        
        if pd.isna(row[col_address]) or not str(row[col_address]).strip():
            errors.append(f"Строка {row_num}: пустой адрес")

        if has_lat or has_lon:
            lat = row[col_lat] if has_lat else None
            lon = row[col_lon] if has_lon else None
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

        # Проверяем даты
        try:
            visit_time_str = str(row[col_visit_time])
            sla_deadline_str = str(row[col_sla_deadline]) if col_sla_deadline else visit_time_str
            
            from app.services.parser import _parse_visit_time, _parse_sla_deadline
            visit_time = _parse_visit_time(visit_time_str)
            sla_deadline = _parse_sla_deadline(sla_deadline_str, visit_time)
            
            if sla_deadline < visit_time:
                errors.append(f"Строка {row_num}: срок выполнения раньше даты визита")
        except Exception:
            errors.append(f"Строка {row_num}: некорректный формат даты визита/срока выполнения")

        # Проверяем тип задачи
        if col_task_type:
            task_type = _map_task_type(str(row[col_task_type]))
            if task_type not in ALLOWED_TASK_TYPES:
                errors.append(f"Строка {row_num}: неизвестный тип задачи '{task_type}' (допустимы: инсталляция, снятие, обследование, устранение неисправностей, доработка)")

        # Проверяем статус
        if has_status:
            status = _map_status(str(row[col_status]))
            if status not in ("scheduled", "assigned", "completed", "postponed"):
                errors.append(f"Строка {row_num}: некорректный статус '{status}' (должен быть scheduled/assigned/completed/postponed)")

    return errors
