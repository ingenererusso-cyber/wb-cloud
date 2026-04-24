from __future__ import annotations

from datetime import date, datetime, timedelta
import time
from typing import Dict, Iterable, List, Tuple

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.db import OperationalError

from core.models import SellerAccount, WbAdvertCampaign, WbAdvertStatDaily
from wb_api.client import WBPromotionClient


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    if isinstance(value, str):
        normalized = value.strip().replace(" ", "").replace(",", ".")
        if not normalized or normalized in {"-", "—", "null", "None"}:
            return float(default)
        value = normalized
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _to_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    parsed = parse_date(str(value))
    if parsed is not None:
        return parsed
    parsed_dt = parse_datetime(str(value))
    if parsed_dt is not None:
        return parsed_dt.date()
    return None


def _to_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif value:
        dt = parse_datetime(str(value))
    else:
        dt = None
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _extract_campaign_start_date(row: Dict) -> date | None:
    timestamps = row.get("timestamps") if isinstance(row.get("timestamps"), dict) else None
    if timestamps:
        for key in ("created", "start", "updated"):
            if key in timestamps:
                dt = _to_datetime(timestamps.get(key))
                if dt is not None:
                    return dt.date()
                d = _to_date(timestamps.get(key))
                if d is not None:
                    return d
    for key in ("startTime", "createTime", "changeTime", "startDate", "createDate", "changeDate"):
        if key in row:
            dt = _to_datetime(row.get(key))
            if dt is not None:
                return dt.date()
            d = _to_date(row.get(key))
            if d is not None:
                return d
    return None


def _update_or_create_with_db_retry(model, *, lookup: Dict, defaults: Dict, attempts: int = 8):
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return model.objects.update_or_create(**lookup, defaults=defaults)
        except OperationalError as exc:
            last_exc = exc
            if "database is locked" not in str(exc).lower() or attempt >= attempts:
                raise
            time.sleep(min(2.0, 0.2 * attempt))
    if last_exc is not None:
        raise last_exc


def _chunks(values: List[int], size: int) -> Iterable[List[int]]:
    for idx in range(0, len(values), size):
        yield values[idx: idx + size]


def _extract_advert_id(row: Dict) -> int | None:
    for key in ("advertId", "advertID", "id", "campaignId"):
        if key in row:
            value = _to_int(row.get(key), default=0)
            if value > 0:
                return value
    return None


def _extract_nm_id(row: Dict) -> int:
    nm_settings = row.get("nm_settings")
    if isinstance(nm_settings, list) and nm_settings:
        first = nm_settings[0]
        if isinstance(first, dict):
            nm_id = _to_int(first.get("nm_id"), default=0)
            if nm_id > 0:
                return nm_id

    for root in ("unitedParams", "autoParams"):
        payload = row.get(root)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            nm_id = _to_int(item.get("nms"), default=0)
            if nm_id > 0:
                return nm_id
    return 0


def _chunk_adverts_by_start_date(
    advert_ids: List[int],
    advert_start_dates: Dict[int, date],
    max_chunk_size: int = 50,
    max_date_span_days: int = 45,
) -> List[List[int]]:
    """
    Группируем ID кампаний в чанки схожих дат запуска:
    - до max_chunk_size ID;
    - разброс дат внутри чанка до max_date_span_days.
    """
    if not advert_ids:
        return []

    with_dates: List[Tuple[int, date]] = []
    without_dates: List[int] = []
    for advert_id in advert_ids:
        d = advert_start_dates.get(int(advert_id))
        if d is None:
            without_dates.append(int(advert_id))
        else:
            with_dates.append((int(advert_id), d))

    with_dates.sort(key=lambda pair: pair[1])
    chunks: List[List[int]] = []
    current: List[int] = []
    current_min: date | None = None
    current_max: date | None = None

    for advert_id, start_d in with_dates:
        can_append = True
        if current and current_min is not None and current_max is not None:
            new_min = min(current_min, start_d)
            new_max = max(current_max, start_d)
            if (new_max - new_min).days > max_date_span_days:
                can_append = False
        if len(current) >= max_chunk_size:
            can_append = False
        if not can_append:
            chunks.append(current)
            current = []
            current_min = None
            current_max = None

        current.append(advert_id)
        current_min = start_d if current_min is None else min(current_min, start_d)
        current_max = start_d if current_max is None else max(current_max, start_d)

    if current:
        chunks.append(current)

    for ids_chunk in _chunks(without_dates, max_chunk_size):
        chunks.append(ids_chunk)

    return chunks


def sync_ad_campaigns_and_stats(
    seller: SellerAccount,
    date_from: date,
    date_to: date,
) -> Dict[str, int]:
    """
    Синк рекламных кампаний и их статистики:
    - список кампаний;
    - дневная статистика по кампаниям и артикулам.
    """
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")

    client = WBPromotionClient(seller.api_token_plain)

    try:
        campaigns_rows = client.list_adverts()
    except Exception as exc:
        return {
            "campaigns_synced": 0,
            "stats_rows_upserted": 0,
            "error": str(exc),
        }
    campaigns_synced = 0
    advert_ids: List[int] = []

    for row in campaigns_rows:
        if not isinstance(row, dict):
            continue
        advert_id = _extract_advert_id(row)
        if not advert_id:
            continue

        advert_ids.append(advert_id)
        _update_or_create_with_db_retry(
            WbAdvertCampaign,
            lookup={
                "seller": seller,
                "advert_id": advert_id,
            },
            defaults={
                "campaign_name": (
                    row.get("name")
                    or row.get("advertName")
                    or ((row.get("settings") or {}).get("name") if isinstance(row.get("settings"), dict) else None)
                    or ""
                ).strip() or None,
                "advert_type": _to_int(row.get("type"), default=0) or None,
                "status": _to_int(row.get("status"), default=0) or None,
                "create_time": _to_datetime(
                    (row.get("timestamps") or {}).get("created")
                    if isinstance(row.get("timestamps"), dict)
                    else row.get("createTime") or row.get("createDate")
                ),
                "change_time": _to_datetime(row.get("changeTime") or row.get("changeDate")),
                "start_time": _to_datetime(row.get("startTime")),
                "end_time": _to_datetime(row.get("endTime")),
                "daily_budget": _to_float(row.get("dailyBudget"), 0.0) or None,
                "raw_payload": row,
            },
        )
        campaigns_synced += 1

    advert_start_dates: Dict[int, date] = {}
    for row in campaigns_rows:
        if not isinstance(row, dict):
            continue
        advert_id = _extract_advert_id(row)
        if not advert_id:
            continue
        start_date = _extract_campaign_start_date(row)
        if start_date is not None:
            advert_start_dates[int(advert_id)] = start_date

    stats_rows_upserted = 0
    if not advert_ids:
        return {
            "campaigns_synced": campaigns_synced,
            "stats_rows_upserted": stats_rows_upserted,
        }

    # Выгружаем статистику по кампаниям type in [8, 9].
    # Исключаем только явно неактуальные/недоступные статусы.
    # Завершенные кампании (status=7) оставляем, чтобы не терять историческую статистику.
    stats_allowed_types = {8, 9}
    excluded_statuses = {-1, 8}
    advert_ids_for_stats: List[int] = []
    for row in campaigns_rows:
        if not isinstance(row, dict):
            continue
        advert_id = _extract_advert_id(row)
        if not advert_id:
            continue
        row_type = _to_int(row.get("type"), default=0)
        row_status = _to_int(row.get("status"), default=0)
        if row_type in stats_allowed_types and row_status not in excluded_statuses:
            advert_ids_for_stats.append(advert_id)

    unique_advert_ids = sorted(set(advert_ids_for_stats))
    if not unique_advert_ids:
        return {
            "campaigns_synced": campaigns_synced,
            "stats_rows_upserted": 0,
        }

    partial_errors: List[str] = []
    # Для ускорения синка используем максимально крупные чанки (до 50 ID),
    # без дополнительного дробления по разбросу дат запуска.
    # Это существенно снижает количество вызовов /adv/v3/fullstats и паузы по rate-limit.
    grouped_id_chunks = list(_chunks(unique_advert_ids, 50))
    # Ограничение WB для advert fullstats фактически ~1 запрос / 20 секунд на кабинет.
    min_fullstats_interval_sec = 20.5
    last_fullstats_request_ts: float | None = None
    today = timezone.localdate()
    effective_date_to = min(date_to, today)
    # WB /adv/v3/fullstats: максимум 31 день истории на запрос.
    max_lookback_from = effective_date_to - timedelta(days=31)
    effective_date_from = max(date_from, max_lookback_from)

    def _respect_fullstats_rate_limit() -> None:
        nonlocal last_fullstats_request_ts
        if last_fullstats_request_ts is not None:
            elapsed = time.monotonic() - last_fullstats_request_ts
            sleep_for = min_fullstats_interval_sec - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    for ids_chunk in grouped_id_chunks:
        chunk_start_dates = [advert_start_dates.get(int(advert_id)) for advert_id in ids_chunk]
        chunk_start_dates = [d for d in chunk_start_dates if d is not None]
        chunk_min_start = min(chunk_start_dates) if chunk_start_dates else effective_date_from
        common_begin = max(effective_date_from, chunk_min_start)
        common_end = effective_date_to
        if common_begin > common_end:
            continue

        try:
            _respect_fullstats_rate_limit()
            stats_rows = client.get_fullstats(
                ids_chunk,
                date_from=common_begin.isoformat(),
                date_to=common_end.isoformat(),
            )
            last_fullstats_request_ts = time.monotonic()
        except Exception as exc:
            last_fullstats_request_ts = time.monotonic()
            # Если чанк не загрузился целиком, пробуем дозагрузить кампании по одной.
            # Это дольше, но не теряет данные по всему чанку из-за одного проблемного ID.
            chunk_error = str(exc)
            recovered_rows: List[Dict] = []
            fallback_errors = 0
            for advert_id_single in ids_chunk:
                single_start = advert_start_dates.get(int(advert_id_single))
                single_begin = max(effective_date_from, single_start) if single_start else effective_date_from
                single_end = effective_date_to
                if single_begin > single_end:
                    continue
                try:
                    _respect_fullstats_rate_limit()
                    one_rows = client.get_fullstats(
                        [int(advert_id_single)],
                        date_from=single_begin.isoformat(),
                        date_to=single_end.isoformat(),
                    )
                    last_fullstats_request_ts = time.monotonic()
                    if isinstance(one_rows, list):
                        recovered_rows.extend(one_rows)
                except Exception as single_exc:
                    last_fullstats_request_ts = time.monotonic()
                    fallback_errors += 1
                    partial_errors.append(
                        f"campaign {int(advert_id_single)}: {single_exc}"
                    )
            if recovered_rows:
                stats_rows = recovered_rows
            else:
                partial_errors.append(
                    f"chunk {ids_chunk[:1]}..{ids_chunk[-1:]} failed: {chunk_error}; fallback_failed={fallback_errors}"
                )
                continue
        if not isinstance(stats_rows, list):
            continue

        for campaign_row in stats_rows:
            if not isinstance(campaign_row, dict):
                continue
            advert_id = _extract_advert_id(campaign_row)
            if not advert_id:
                continue

            days = campaign_row.get("days") or campaign_row.get("dates") or []
            if not isinstance(days, list):
                continue

            for day_row in days:
                if not isinstance(day_row, dict):
                    continue
                stat_date = _to_date(day_row.get("date"))
                if stat_date is None:
                    continue

                day_views = _to_int(day_row.get("views"), default=0)
                day_clicks = _to_int(day_row.get("clicks"), default=0)
                day_orders = _to_int(day_row.get("orders"), default=0)
                day_atc = _to_int(day_row.get("atbs"), default=0)
                day_spend = _to_float(day_row.get("sum"), 0.0)

                apps = day_row.get("apps") or []
                nm_rows_written = 0
                if isinstance(apps, list):
                    for app_row in apps:
                        if not isinstance(app_row, dict):
                            continue
                        nm_rows = app_row.get("nm") or app_row.get("nms") or []
                        if not isinstance(nm_rows, list):
                            continue
                        for nm_row in nm_rows:
                            if not isinstance(nm_row, dict):
                                continue
                            nm_id = _to_int(
                                nm_row.get("nmId", nm_row.get("nmID", nm_row.get("id"))),
                                default=0,
                            )
                            if nm_id <= 0:
                                continue
                            nm_spend = _to_float(nm_row.get("sum"), 0.0)
                            _update_or_create_with_db_retry(
                                WbAdvertStatDaily,
                                lookup={
                                    "seller": seller,
                                    "advert_id": advert_id,
                                    "stat_date": stat_date,
                                    "nm_id": nm_id,
                                },
                                defaults={
                                    "spend": nm_spend,
                                    "views": None,
                                    "clicks": None,
                                    "orders": None,
                                    "add_to_cart": None,
                                    "raw_payload": {
                                        "campaign": campaign_row,
                                        "day": day_row,
                                        "app": app_row,
                                        "nm": nm_row,
                                    },
                                },
                            )
                            stats_rows_upserted += 1
                            nm_rows_written += 1

                if nm_rows_written == 0:
                    # Если разбивки по артикулам нет, сохраняем агрегатной строкой nm_id=0.
                    _update_or_create_with_db_retry(
                        WbAdvertStatDaily,
                        lookup={
                            "seller": seller,
                            "advert_id": advert_id,
                            "stat_date": stat_date,
                            "nm_id": 0,
                        },
                        defaults={
                            "spend": day_spend,
                            "views": day_views,
                            "clicks": day_clicks,
                            "orders": day_orders,
                            "add_to_cart": day_atc,
                            "raw_payload": {
                                "campaign": campaign_row,
                                "day": day_row,
                            },
                        },
                    )
                    stats_rows_upserted += 1

    result = {
        "campaigns_synced": campaigns_synced,
        "stats_rows_upserted": stats_rows_upserted,
    }
    if partial_errors:
        result["error"] = f"Часть статистики рекламы пропущена ({len(partial_errors)} чанков). Последняя ошибка: {partial_errors[-1]}"
    return result
