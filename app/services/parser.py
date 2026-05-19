from __future__ import annotations

from datetime import datetime
import re

import pandas as pd

from app.schemas import TaskItem


# Поддерживаемые варианты названий колонок (английские и русские)
COLUMN_MAPPING = {
    "task_id": ["task_id", "№ задачи", "№", "id", "ID", "Задание №", "Л/С", "№задачи", "задача", "ID задачи", "Номер задачи", "Задание", "№ Л/С"],
    "address": ["address", "Адрес", "адрес", "Адрес объекта"],
    "visit_time": ["visit_time", "Дата визита", "дата визита", "Время визита", "Дата и время", "Дата и срок задания м."],
    "sla_deadline": ["sla_deadline", "Срок выполнения", "срок выполнения", "Дедлайн", "deadline", "Срок задания м.", "Срок заявки м."],
    "task_type": ["task_type", "Тип задачи", "тип задачи", "Вид работы", "Тип задания"],
    "client_type": ["client_type", "Тип клиента", "тип клиента", "Клиент"],
    "duration_min": ["duration_min", "Длительность", "длительность", "Время (мин)", "минуты", "До окончания срока процесса", "До конца заявки"],
    "lat": ["lat", "Широта", "широта", "Latitude", "Lat"],
    "lon": ["lon", "Долгота", "долгота", "Longitude", "Lon"],
    "status": ["status", "Статус", "статус", "Состояние задания"],
    "formatted_address": ["formatted_address", "Рабочий адрес", "Форматированный адрес"],
    "geocode_status": ["geocode_status", "Статус геокодирования"],
}


def _normalize_column_name(name: str) -> str:
    """Нормализует название колонки: убирает пробелы, приводит к нижнему регистру, удаляет спецсимволы."""
    import re
    # Убираем пробелы в начале/конце и приводим к нижнему регистру
    normalized = str(name).strip().lower()
    # Убираем лишние пробелы между словами
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized


def _find_column(df: pd.DataFrame, standard_name: str) -> str | None:
    """Находит колонку в DataFrame по стандартному названию или его русским аналогам."""
    possible_names = COLUMN_MAPPING.get(standard_name, [standard_name])
    
    # Нормализуем все колонки DataFrame
    df_columns_normalized = {
        _normalize_column_name(col): col 
        for col in df.columns
    }
    
    # Проверяем каждое возможное название
    for name in possible_names:
        normalized = _normalize_column_name(name)
        if normalized in df_columns_normalized:
            return df_columns_normalized[normalized]
    
    # Если не нашли по mapping, пробуем частичное совпадение
    for df_col_norm, df_col_orig in df_columns_normalized.items():
        for possible in possible_names:
            possible_norm = _normalize_column_name(possible)
            if possible_norm in df_col_norm or df_col_norm in possible_norm:
                return df_col_orig
    
    return None


def _parse_visit_time(value: str) -> datetime:
    """Парсит дату визита из формата '22.04.2026 09:00 - 21:00' или '22.04.2026 09:00'."""
    if pd.isna(value) or not value or str(value).strip() == "":
        return datetime.now()
    
    s = str(value).strip()
    
    # Пробуем формат с диапазоном: "22.04.2026 09:00 - 21:00" или "22.04.2026 09:00-21:00"
    match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})\s*[-–]\s*(\d{2}):(\d{2})', s)
    if match:
        day, month, year, hour, minute, _, _ = match.groups()
        return datetime(int(year), int(month), int(day), int(hour), int(minute))
    
    # Пробуем формат без диапазона: "22.04.2026 09:00" или "22.04.2026 09:00:00"
    match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?', s)
    if match:
        day, month, year, hour, minute, _ = match.groups()
        return datetime(int(year), int(month), int(day), int(hour), int(minute))
    
    # Пробуем через pandas как последний вариант
    try:
        parsed = pd.to_datetime(s, dayfirst=True, errors='coerce')
        if pd.notna(parsed):
            return parsed.to_pydatetime()
    except Exception:
        pass
    
    # Если ничего не вышло — возвращаем текущее время
    print(f"   ⚠️ Не удалось распарсить дату: '{s}'")
    return datetime.now()


def _parse_visit_window(value: str) -> tuple[datetime, datetime | None]:
    """Парсит начало и конец интервала визита из строки вида '22.04.2026 12:00 - 15:00'."""
    if pd.isna(value) or not value or str(value).strip() == "":
        start = datetime.now()
        return start, None

    s = str(value).strip()
    match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})\s*[-–]\s*(\d{2}):(\d{2})', s)
    if match:
        day, month, year, start_hour, start_minute, end_hour, end_minute = match.groups()
        start = datetime(int(year), int(month), int(day), int(start_hour), int(start_minute))
        end = datetime(int(year), int(month), int(day), int(end_hour), int(end_minute))
        return start, end

    start = _parse_visit_time(value)
    return start, None


def _parse_sla_deadline(value: str, visit_time: datetime) -> datetime:
    """Парсит срок выполнения из формата '22.04.2026 21:00:00' или диапазона."""
    if pd.isna(value):
        return visit_time
    
    s = str(value).strip()
    
    # Пробуем формат "22.04.2026 21:00:00"
    match = re.match(r'(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2}:\d{2})', s)
    if match:
        date_str = match.group(1)
        time_str = match.group(2)
        return datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M:%S")
    
    # Пробуем формат "22.04.2026 21:00"
    match = re.match(r'(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})', s)
    if match:
        date_str = match.group(1)
        time_str = match.group(2)
        return datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    
    # По умолчанию - конец дня визита
    return visit_time.replace(hour=23, minute=59, second=59)


def _map_task_type(value: str) -> str:
    """Преобразует тип задачи из русского формата в стандартный."""
    if pd.isna(value):
        return "инсталляция"
    
    s = str(value).strip().lower()
    
    if "инсталляция" in s or "установка" in s:
        return "инсталляция"
    elif "снятие" in s:
        return "снятие"
    elif "обследование" in s or "осмотр" in s:
        return "обследование"
    elif "устранение неисправности" in s or "неисправность" in s or "техподдержка" in s:
        return "устранение неисправностей"
    elif "дополнительные работы" in s or "доработка" in s:
        return "доработка"
    
    return "инсталляция"


def _map_status(value: str) -> str:
    """Преобразует статус задачи в стандартный формат."""
    if pd.isna(value):
        return "scheduled"
    
    s = str(value).strip().lower()
    
    if "назначена" in s or "assigned" in s:
        return "assigned"
    elif "отложена" in s or "postponed" in s:
        return "postponed"
    elif "выполнена" in s or "completed" in s or "завершена" in s:
        return "completed"
    elif "запланирована" in s or "scheduled" in s:
        return "scheduled"
    
    return "scheduled"


def _extract_duration_minutes(value: str) -> int:
    """Извлекает длительность в минутах из строки вида '1 д. 6 ч. 27 мин. 9 сек.'."""
    if pd.isna(value):
        return 60  # По умолчанию 1 час
    
    s = str(value).strip().lower()
    
    total_minutes = 0
    
    # Дни
    days_match = re.search(r'(\d+)\s*д\.', s)
    if days_match:
        total_minutes += int(days_match.group(1)) * 24 * 60
    
    # Часы
    hours_match = re.search(r'(\d+)\s*ч\.', s)
    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60
    
    # Минуты
    mins_match = re.search(r'(\d+)\s*мин\.', s)
    if mins_match:
        total_minutes += int(mins_match.group(1))
    
    # Секунды (округляем до минут)
    secs_match = re.search(r'(\d+)\s*сек\.', s)
    if secs_match:
        total_minutes += int(secs_match.group(1)) // 60
    
    return max(total_minutes, 30)  # Минимум 30 минут


def _default_duration_for_type(task_type: str) -> int:
    """
    В исходных выгрузках часто нет реальной длительности работ, а встречается поле
    "до окончания срока" (SLA slack), которое нельзя трактовать как длительность.
    Поэтому используем разумные дефолты по типу работ.
    """
    tt = (task_type or "").strip().lower()
    if tt in ("техподдержка", "устранение неисправностей"):
        return 45
    if tt == "снятие":
        return 45
    if tt == "обследование":
        return 60
    if tt == "доработка":
        return 90
    return 60  # инсталляция и прочее


def _sanitize_duration_minutes(duration_min: int) -> int:
    # Ограничиваем длительность в адекватных пределах для планирования смены.
    # Большие значения в файле почти всегда означают не длительность работ, а "время до SLA".
    return max(30, min(int(duration_min), 240))


def parse_tasks(df: pd.DataFrame) -> list[TaskItem]:
    tasks: list[TaskItem] = []
    
    # Находим необходимые колонки
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
    col_formatted_address = _find_column(df, "formatted_address")
    col_geocode_status = _find_column(df, "geocode_status")
    
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
        raise ValueError(f"Отсутствуют обязательные колонки: {', '.join(missing)}")
    
    for idx, row in df.iterrows():
        # Парсим значения с учётом формата файла
        visit_time_str = str(row[col_visit_time]) if col_visit_time else ""
        sla_deadline_str = str(row[col_sla_deadline]) if col_sla_deadline else ""
        
        visit_time, visit_end = _parse_visit_window(visit_time_str) if col_visit_time else (datetime.now(), None)
        sla_deadline = _parse_sla_deadline(sla_deadline_str, visit_time) if col_sla_deadline else visit_time
        
        task_type = _map_task_type(str(row[col_task_type])) if col_task_type else "инсталляция"

        # Длительность: в выгрузках часто лежит "до конца SLA" (дни/часы) — это НЕ длительность работ.
        # Поэтому: если длительность выглядит как "д./ч./мин." — игнорируем и берём дефолт по типу.
        duration_min = _default_duration_for_type(task_type)
        if col_duration_min:
            duration_raw = row[col_duration_min]
            if pd.notna(duration_raw):
                if isinstance(duration_raw, (int, float)):
                    duration_min = _sanitize_duration_minutes(int(duration_raw))
                else:
                    duration_str = str(duration_raw).strip()
                    try:
                        # Иногда там реально минутами числом
                        duration_min = _sanitize_duration_minutes(int(float(duration_str)))
                    except ValueError:
                        # Строковые форматы "X ч. Y мин." считаем только если это НЕ дни (д.)
                        # Если есть "д.", считаем это SLA slack и игнорируем.
                        if "д." not in duration_str.lower():
                            duration_min = _sanitize_duration_minutes(_extract_duration_minutes(duration_str))
        
        status = _map_status(str(row[col_status])) if col_status else "scheduled"
        
        tasks.append(
            TaskItem(
                task_id=str(row[col_task_id]),
                address=str(row[col_address]),
                formatted_address=(
                    str(row[col_formatted_address])
                    if col_formatted_address and pd.notna(row[col_formatted_address])
                    else str(row[col_address])
                ),
                lat=_to_float(row[col_lat]) if col_lat else None,
                lon=_to_float(row[col_lon]) if col_lon else None,
                visit_time=visit_time,
                visit_end=visit_end,
                sla_deadline=sla_deadline,
                task_type=task_type,
                client_type=str(row[col_client_type]) if col_client_type else "физлицо",
                duration_min=duration_min,
                status=status,
                geocode_status=str(row[col_geocode_status]) if col_geocode_status and pd.notna(row[col_geocode_status]) else None,
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
