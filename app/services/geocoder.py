from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
import json
from typing import Optional

import aiohttp
import pandas as pd

# Кэш в памяти (для текущей сессии)
_geocode_cache: dict[str, tuple[float, float]] = {}

# Файловый кэш — сохраняется между перезапусками
_CACHE_FILE = Path("geocode_cache.json")
_USE_FILE_CACHE = os.getenv("RTK_USE_GEOCODE_CACHE", "0") == "1"
_SAVE_FILE_CACHE = os.getenv("RTK_SAVE_GEOCODE_CACHE", "0") == "1"

_RATE_LIMIT_DELAY = 1.1  # Лимит Nominatim: 1 запрос/сек


def _load_file_cache():
    """Загружает кэш из файла при старте."""
    if not _USE_FILE_CACHE:
        return
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            _geocode_cache.update({k: tuple(v) for k, v in data.items()})
            print(f"[geocoder] Loaded {len(_geocode_cache)} cached addresses")
        except Exception:
            pass


def _save_file_cache():
    """Сохраняет кэш в файл."""
    if not _SAVE_FILE_CACHE:
        return
    try:
        _CACHE_FILE.write_text(
            json.dumps({k: list(v) for k, v in _geocode_cache.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# Загружаем кэш при импорте модуля
_load_file_cache()


def clean_address(raw: str) -> str:
    """
    Приводит адрес формата «обл. ИРКУТСКАЯ г. ИРКУТСК ул. РАКИТНАЯ д. 18а»
    к виду «улица Ракитная 18, Иркутск» — понятному для Nominatim.
    Лишние уточнения (квартира, кабинет, павильон, литера, вход и т.п.) отрезаются:
    геокодеру нужен дом, а не помещение внутри здания.
    """
    s = str(raw).strip()
    s = re.sub(r'\s+', ' ', s)

    # Убираем служебные хвосты, которые часто ломают геокодирование.
    stop_words = (
        r'кв\.?|квартира|оф\.?|офис|каб\.?|кабинет|пав\.?|павильон|'
        r'пом\.?|помещение|лит\.?|литер|литера|эт\.?|этаж|под\.?|подъезд|'
        r'вход|строение|стр\.?|комната|ком\.?|секция|блок'
    )
    s = re.split(rf'(?i)\b(?:{stop_words})\b', s, maxsplit=1)[0]

    # Убираем всё до первого «ул.» / «пр.» / «пер.» / «пл.» / «б-р» / «наб.» / «ш.»
    # но сохраняем саму метку и то, что после неё
    street_patterns = [
        r'(?i)(ул\.?\s+)',
        r'(?i)(улица\s+)',
        r'(?i)(пр-т\.?\s+|пр\.?\s+|проспект\s+)',
        r'(?i)(пер\.?\s+|переулок\s+)',
        r'(?i)(пл\.?\s+|площадь\s+)',
        r'(?i)(б-р\.?\s+|бульвар\s+)',
        r'(?i)(наб\.?\s+|набережная\s+)',
        r'(?i)(ш\.?\s+|шоссе\s+)',
    ]

    best_idx = len(s)
    for pat in street_patterns:
        m = re.search(pat, s)
        if m and m.start() < best_idx:
            best_idx = m.start()

    if best_idx < len(s):
        s = s[best_idx:]

    # Убираем «д.» перед номером дома — Nominatim лучше понимает без него.
    s = re.sub(r'\bд(?:ом)?\.?\s*', '', s, flags=re.IGNORECASE)

    # Дом 18а/18А/18/1 для геокодера упрощаем до 18 по просьбе заказчика:
    # дроби, литеры и внутренние корпуса чаще мешают, чем помогают.
    s = re.sub(r'(\d+)\s*[а-яёa-z]?\s*/\s*\d+[а-яёa-z]?', r'\1', s, flags=re.IGNORECASE)
    s = re.sub(r'(\d+)\s*[-–]\s*\d+', r'\1', s)
    s = re.sub(r'(\d+)\s*[а-яёa-z]\b', r'\1', s, flags=re.IGNORECASE)

    # Убираем остатки внутренних обозначений.
    s = re.sub(r'\bкорп\.?\s*\d+\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bк\.?\s*\d+\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bПП-\d+.*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[,;]+', ' ', s)

    # Убираем лишние пробелы и приводим к нижнему регистру
    s = ' '.join(s.split()).strip()

    # Принудительно добавляем город — Nominatim ищет точнее с ним
    if 'иркутск' not in s.lower():
        s = f"{s}, Иркутск"

    return s


def _legacy_clean_address(raw: str) -> str:
    """
    Мягкая очистка, близкая к прежней логике. Она чаще сохраняет корпус/литеру,
    которые иногда помогают Nominatim найти адрес.
    """
    s = str(raw).strip()
    s = re.sub(r'\s+', ' ', s)
    s = _strip_to_street(s)

    s = re.sub(r'\bд\.\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bкв\..*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bэт\..*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bпод\..*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bПП-\d+.*$', '', s, flags=re.IGNORECASE)
    s = " ".join(s.split()).strip(" ,;")
    return _ensure_irkutsk(s)


def _medium_clean_address(raw: str) -> str:
    """
    Средняя очистка: убираем кабинеты/павильоны/офисы, но не ломаем номер дома
    до самого строгого варианта.
    """
    s = str(raw).strip()
    s = re.sub(r'\s+', ' ', s)
    s = _strip_to_street(s)
    s = re.sub(r'\bд(?:ом)?\.?\s*', '', s, flags=re.IGNORECASE)
    s = re.split(
        r'(?i)\b(?:кв\.?|квартира|оф\.?|офис|каб\.?|кабинет|пав\.?|павильон|'
        r'пом\.?|помещение|эт\.?|этаж|под\.?|подъезд|вход|комната|ком\.?)\b',
        s,
        maxsplit=1,
    )[0]
    s = re.sub(r'[,;]+', ' ', s)
    s = " ".join(s.split()).strip(" ,;")
    return _ensure_irkutsk(s)


def _strip_to_street(value: str) -> str:
    street_patterns = [
        r'(?i)(ул\.?\s+)',
        r'(?i)(улица\s+)',
        r'(?i)(пр-т\.?\s+|пр\.?\s+|проспект\s+)',
        r'(?i)(пер\.?\s+|переулок\s+)',
        r'(?i)(пл\.?\s+|площадь\s+)',
        r'(?i)(б-р\.?\s+|бульвар\s+)',
        r'(?i)(наб\.?\s+|набережная\s+)',
        r'(?i)(ш\.?\s+|шоссе\s+)',
        r'(?i)(мкр\.?\s+|микрорайон\s+)',
    ]
    best_idx = len(value)
    for pat in street_patterns:
        m = re.search(pat, value)
        if m and m.start() < best_idx:
            best_idx = m.start()
    return value[best_idx:] if best_idx < len(value) else value


def _ensure_irkutsk(value: str) -> str:
    s = " ".join(str(value).split()).strip(" ,;")
    if s and "иркутск" not in s.lower():
        s = f"{s}, Иркутск"
    return s


def _expand_street_words(value: str) -> str:
    return (
        value.replace("ул. ", "улица ")
        .replace("пр. ", "проспект ")
        .replace("пр-т ", "проспект ")
        .replace("пер. ", "переулок ")
        .replace("пл. ", "площадь ")
        .replace("наб. ", "набережная ")
        .replace("ш. ", "шоссе ")
        .replace("мкр. ", "микрорайон ")
    )


async def _geocode_one(session: aiohttp.ClientSession, address: str) -> Optional[tuple[float, float]]:
    """Один актуальный запрос к Nominatim. Кэш не подменяет внешний поиск."""

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "ru",
        "accept-language": "ru",
    }
    headers = {"User-Agent": "RTK-Brigade-Planner/1.0 (contact@irkutsk.rtk)"}

    try:
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                if data:
                    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                    _geocode_cache[address] = (lat, lon)
                    _save_file_cache()
                    return (lat, lon)
    except Exception as e:
        print(f"[geocoder] Nominatim error for '{address}': {e}")

    return None

def _candidate_queries(raw: str) -> list[str]:
    """
    Nominatim иногда не находит адрес после агрессивной очистки.
    Поэтому пробуем несколько вариантов: сначала старую мягкую очистку,
    затем среднюю, потом строгую для адресов с литерами/дробями.
    """
    raw_s = str(raw).strip()
    legacy = _legacy_clean_address(raw_s)
    medium = _medium_clean_address(raw_s)
    strict = clean_address(raw_s)

    candidates = [
        legacy,
        _expand_street_words(legacy),
        medium,
        _expand_street_words(medium),
        strict,
        _expand_street_words(strict),
    ]

    # Пробуем исходную строку как есть (часто там есть микрорайон/ориентир)
    if raw_s and raw_s.lower() not in {legacy.lower(), medium.lower(), strict.lower()}:
        candidates.append(raw_s)
        if "иркутск" not in raw_s.lower():
            candidates.append(f"{raw_s}, Иркутск")
            candidates.append(f"{raw_s}, Иркутск, Россия")

    # И ещё вариант с регионом (иногда выручает)
    for base in (legacy, medium, strict):
        if base and "россия" not in base.lower():
            candidates.append(f"{base}, Россия")
            candidates.append(f"{base}, Иркутская область, Россия")

    return _dedupe_queries(candidates)


async def _geocode_with_fallback(session: aiohttp.ClientSession, raw_address: str) -> tuple[Optional[tuple[float, float]], str]:
    queries = _candidate_queries(raw_address)
    for idx, q in enumerate(queries):
        coords = await _geocode_one(session, q)
        if coords:
            return coords, "ok"
        if idx < len(queries) - 1:
            await asyncio.sleep(_RATE_LIMIT_DELAY)

    return None, "not_found"


def _dedupe_queries(candidates: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for c in candidates:
        c = " ".join(str(c).split()).strip()
        if not c:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


async def geocode_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """
    Добавляет lat/lon в DataFrame если их нет.
    Очищает адреса формата «Технограда» перед отправкой в Nominatim.
    """
    has_lat = "lat" in df.columns
    has_lon = "lon" in df.columns

    # Если координаты есть частично — геокодируем только пропуски.
    if has_lat and has_lon:
        try:
            if df["lat"].notna().all() and df["lon"].notna().all():
                address_col = _find_address_column(df)
                if address_col and "formatted_address" not in df.columns:
                    df["formatted_address"] = df[address_col].apply(clean_address)
                if "geocode_status" not in df.columns:
                    df["geocode_status"] = "ok"
                return df  # Координаты уже есть для всех строк
        except Exception:
            pass

    # Ищем колонку с адресом (не только точное совпадение, но и "содержит 'адрес'")
    address_col = _find_address_column(df)

    if not address_col:
        print("[geocoder] Address column not found")
        return df

    # Уникальные адреса (только те строки, где нет координат)
    work_df = df
    if has_lat and has_lon:
        work_df = df[df["lat"].isna() | df["lon"].isna()]

    raw_addresses = work_df[address_col].dropna().unique().tolist()
    raw_addresses = [str(a).strip() for a in raw_addresses if str(a).strip()]

    print(f"\n[geocoder] Geocoding {len(raw_addresses)} addresses...")

    # Маппинг: оригинальный адрес -> (координаты, статус)
    results: dict[str, tuple[Optional[tuple[float, float]], str]] = {}

    async with aiohttp.ClientSession() as session:
        for i, raw in enumerate(raw_addresses):
            coords = None
            did_request = False
            clean = clean_address(raw)
            did_request = True
            coords, status = await _geocode_with_fallback(session, raw)
            if coords:
                # Точный результат можно держать в памяти текущего запуска, но
                # дисковый кэш по умолчанию отключен.
                if status == "ok":
                    _geocode_cache[clean] = coords
                    _save_file_cache()
                print(f"[geocoder] {i+1}/{len(raw_addresses)} (exact): {clean} -> {coords}")
            else:
                status = "not_found"
                print(f"[geocoder] {i+1}/{len(raw_addresses)}: NOT FOUND: {clean}")

            results[raw] = (coords, status)

            # лимитим только реальные запросы (кэш не ждём)
            if i < len(raw_addresses) - 1 and did_request:
                await asyncio.sleep(_RATE_LIMIT_DELAY)

    # Добавляем в DataFrame
    if "lat" not in df.columns:
        df["lat"] = None
    if "lon" not in df.columns:
        df["lon"] = None
    if "formatted_address" not in df.columns:
        df["formatted_address"] = None
    if "geocode_status" not in df.columns:
        df["geocode_status"] = None

    found_rows = 0
    for idx, row in df.iterrows():
        raw = str(row[address_col]).strip() if pd.notna(row[address_col]) else ""
        if raw:
            df.at[idx, "formatted_address"] = clean_address(raw)
        if raw and raw in results and results[raw][0]:
            coords, status = results[raw]
            df.at[idx, "lat"] = coords[0]
            df.at[idx, "lon"] = coords[1]
            df.at[idx, "geocode_status"] = status
            found_rows += 1
        elif raw:
            df.at[idx, "geocode_status"] = "not_found"

    found_unique = sum(1 for coords, _ in results.values() if coords)
    total_rows = sum(1 for _, row in df.iterrows() if pd.notna(row[address_col]) and str(row[address_col]).strip())

    print(
        "\n[geocoder] Found "
        f"{found_unique}/{len(raw_addresses)} unique addresses "
        f"rows {found_rows}/{total_rows}\n"
    )
    return df


def _normalize_col(col: str) -> str:
    return str(col).strip().lower()


def _find_address_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        normalized = str(col).strip().lower()
        if normalized in ["адрес", "address", "адрес объекта"]:
            return col
    for col in df.columns:
        normalized = str(col).strip().lower()
        if "адрес" in normalized:
            return col
    return None
