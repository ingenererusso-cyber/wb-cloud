from __future__ import annotations

from datetime import date, datetime, timedelta
import time
from typing import Dict, Iterable, List, Tuple

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.models import SellerAccount, WbAdvertCampaign, WbAdvertStatDaily
from wb_api.client import WBPromotionClient


FULLSTATS_MIN_REQUEST_INTERVAL_SEC = 20.5
AD_STATS_BULK_CREATE_BATCH_SIZE = 200
AD_STATS_BULK_UPDATE_BATCH_SIZE = 100
AD_STATS_EXISTING_LOOKUP_BATCH_SIZE = 200


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


def _compact_payload(row: Dict | None, allowed_keys: Iterable[str]) -> Dict:
    if not isinstance(row, dict):
        return {}
    payload: Dict = {}
    for key in allowed_keys:
        value = row.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _compact_fullstats_payload(
    *,
    campaign_row: Dict,
    day_row: Dict,
    app_row: Dict | None = None,
    nm_row: Dict | None = None,
) -> Dict:
    """
    Store only fields used by dashboards/details.

    WB fullstats responses can contain nested arrays with all days/apps/nm rows.
    Saving that full object for every daily/nm stat duplicates JSON massively and
    can make PostgreSQL bulk updates allocate gigabytes of memory.
    """
    payload = {
        "campaign": _compact_payload(
            campaign_row,
            (
                "advertId",
                "advertID",
                "id",
                "campaignId",
                "name",
                "advertName",
                "type",
                "status",
            ),
        ),
        "day": _compact_payload(
            day_row,
            (
                "date",
                "views",
                "clicks",
                "orders",
                "atbs",
                "sum",
            ),
        ),
    }
    if app_row is not None:
        payload["app"] = _compact_payload(app_row, ("appType", "appName", "name"))
    if nm_row is not None:
        payload["nm"] = _compact_payload(
            nm_row,
            (
                "nmId",
                "nmID",
                "id",
                "name",
                "views",
                "clicks",
                "orders",
                "atbs",
                "sum",
                "sum_price",
                "sumPrice",
                "ordersSumRub",
            ),
        )
    return payload


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
    campaign_statuses: List[int] | None = None,
    on_progress=None,
    fullstats_rate_state: Dict[str, float] | None = None,
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
        campaigns_rows = client.list_adverts(statuses=campaign_statuses)
    except Exception as exc:
        return {
            "campaigns_synced": 0,
            "stats_rows_upserted": 0,
            "error": str(exc),
        }
    campaigns_synced = 0
    advert_ids: List[int] = []
    campaign_rows_map: Dict[int, Dict] = {}
    campaigns_now = timezone.now()

    for row in campaigns_rows:
        if not isinstance(row, dict):
            continue
        advert_id = _extract_advert_id(row)
        if not advert_id:
            continue

        advert_ids.append(advert_id)
        campaign_rows_map[int(advert_id)] = {
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
            "updated_at": campaigns_now,
        }
    if campaign_rows_map:
        existing_campaign_map = {
            int(item.advert_id): item
            for item in WbAdvertCampaign.objects.filter(
                seller=seller,
                advert_id__in=campaign_rows_map.keys(),
            )
        }
        campaign_update_fields = [
            "campaign_name",
            "advert_type",
            "status",
            "create_time",
            "change_time",
            "start_time",
            "end_time",
            "daily_budget",
            "raw_payload",
            "updated_at",
        ]
        to_create_campaigns: List[WbAdvertCampaign] = []
        to_update_campaigns: List[WbAdvertCampaign] = []
        for advert_id, defaults in campaign_rows_map.items():
            existing = existing_campaign_map.get(advert_id)
            if existing is None:
                to_create_campaigns.append(
                    WbAdvertCampaign(
                        seller=seller,
                        advert_id=advert_id,
                        **defaults,
                    )
                )
                continue
            for field_name in campaign_update_fields:
                setattr(existing, field_name, defaults[field_name])
            to_update_campaigns.append(existing)
        if to_create_campaigns:
            WbAdvertCampaign.objects.bulk_create(to_create_campaigns, batch_size=2000)
        if to_update_campaigns:
            WbAdvertCampaign.objects.bulk_update(to_update_campaigns, campaign_update_fields, batch_size=2000)
        campaigns_synced = len(campaign_rows_map)

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

    today = timezone.localdate()
    effective_date_to = min(date_to, today)
    # WB /adv/v3/fullstats: максимум 31 день истории на запрос.
    max_lookback_from = effective_date_to - timedelta(days=31)
    effective_date_from = max(date_from, max_lookback_from)

    eligible_advert_ids = []
    for advert_id in unique_advert_ids:
        start_date = advert_start_dates.get(int(advert_id))
        if start_date is not None and start_date > effective_date_to:
            continue
        eligible_advert_ids.append(int(advert_id))
    if not eligible_advert_ids:
        return {
            "campaigns_synced": campaigns_synced,
            "stats_rows_upserted": 0,
        }

    partial_errors: List[str] = []
    skipped_chunks: List[Dict[str, object]] = []
    # Для ускорения синка используем максимально крупные чанки (до 50 ID),
    # без дополнительного дробления по разбросу дат запуска.
    # Это существенно снижает количество вызовов /adv/v3/fullstats и паузы по rate-limit.
    grouped_id_chunks = list(_chunks(eligible_advert_ids, 50))
    # Ограничение WB для advert fullstats фактически ~1 запрос / 20 секунд на кабинет.
    rate_state = fullstats_rate_state if isinstance(fullstats_rate_state, dict) else {}

    def _fullstats_wait_seconds() -> float:
        last_fullstats_request_ts = rate_state.get("last_fullstats_request_ts")
        if last_fullstats_request_ts is not None:
            elapsed = time.monotonic() - float(last_fullstats_request_ts)
            return max(0.0, FULLSTATS_MIN_REQUEST_INTERVAL_SEC - elapsed)
        return 0.0

    def _mark_fullstats_request() -> None:
        rate_state["last_fullstats_request_ts"] = time.monotonic()

    def _bulk_upsert_stats(stat_rows_map: Dict[tuple[int, date, int], Dict]) -> int:
        if not stat_rows_map:
            return 0
        advert_ids_for_map = sorted({row_key[0] for row_key in stat_rows_map.keys()})
        stat_dates_for_map = sorted({row_key[1] for row_key in stat_rows_map.keys()})
        min_stat_date = min(stat_dates_for_map)
        max_stat_date = max(stat_dates_for_map)
        target_keys = set(stat_rows_map.keys())
        existing_stat_map: Dict[tuple[int, date, int], WbAdvertStatDaily] = {}
        for advert_chunk in _chunks(advert_ids_for_map, AD_STATS_EXISTING_LOOKUP_BATCH_SIZE):
            for item in WbAdvertStatDaily.objects.filter(
                seller=seller,
                advert_id__in=advert_chunk,
                stat_date__gte=min_stat_date,
                stat_date__lte=max_stat_date,
            ).only("id", "advert_id", "stat_date", "nm_id"):
                item_key = (int(item.advert_id), item.stat_date, _to_int(item.nm_id, default=0))
                if item_key in target_keys:
                    existing_stat_map[item_key] = item
        update_fields = ["spend", "day_sum", "views", "clicks", "orders", "add_to_cart", "raw_payload", "updated_at"]
        to_create_stats: List[WbAdvertStatDaily] = []
        to_update_stats: List[WbAdvertStatDaily] = []
        for (advert_id, stat_date, nm_id), defaults in stat_rows_map.items():
            existing = existing_stat_map.get((advert_id, stat_date, nm_id))
            if existing is None:
                to_create_stats.append(
                    WbAdvertStatDaily(
                        seller=seller,
                        advert_id=advert_id,
                        stat_date=stat_date,
                        nm_id=nm_id,
                        **defaults,
                    )
                )
                continue
            for field_name in update_fields:
                setattr(existing, field_name, defaults[field_name])
            to_update_stats.append(existing)
        if to_create_stats:
            WbAdvertStatDaily.objects.bulk_create(to_create_stats, batch_size=AD_STATS_BULK_CREATE_BATCH_SIZE)
        if to_update_stats:
            WbAdvertStatDaily.objects.bulk_update(to_update_stats, update_fields, batch_size=AD_STATS_BULK_UPDATE_BATCH_SIZE)
        return len(stat_rows_map)

    total_chunks = len(grouped_id_chunks)
    for chunk_index, ids_chunk in enumerate(grouped_id_chunks, start=1):
        stat_rows_map: Dict[tuple[int, date, int], Dict] = {}
        stats_now = timezone.now()
        chunk_start_dates = [advert_start_dates.get(int(advert_id)) for advert_id in ids_chunk]
        chunk_start_dates = [d for d in chunk_start_dates if d is not None]
        chunk_min_start = min(chunk_start_dates) if chunk_start_dates else effective_date_from
        common_begin = max(effective_date_from, chunk_min_start)
        common_end = effective_date_to
        if common_begin > common_end:
            continue

        if callable(on_progress):
            on_progress(
                {
                    "mode": "chunk",
                    "chunk_index": chunk_index,
                    "chunks_total": total_chunks,
                    "chunk_size": len(ids_chunk),
                    "date_from": common_begin,
                    "date_to": common_end,
                    "advert_ids": list(ids_chunk),
                }
            )

        try:
            wait_seconds = _fullstats_wait_seconds()
            if wait_seconds > 0 and callable(on_progress):
                on_progress(
                    {
                        "mode": "rate_limit_wait",
                        "chunk_index": chunk_index,
                        "chunks_total": total_chunks,
                        "chunk_size": len(ids_chunk),
                        "date_from": common_begin,
                        "date_to": common_end,
                        "advert_ids": list(ids_chunk),
                        "wait_seconds": wait_seconds,
                    }
                )
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            stats_rows = client.get_fullstats(
                ids_chunk,
                date_from=common_begin.isoformat(),
                date_to=common_end.isoformat(),
            )
            _mark_fullstats_request()
        except Exception as exc:
            _mark_fullstats_request()
            chunk_error = str(exc).strip() or exc.__class__.__name__
            chunk_summary = (
                f"Чанк {chunk_index}/{total_chunks} "
                f"({common_begin.isoformat()} - {common_end.isoformat()}, "
                f"{len(ids_chunk)} РК) пропущен: {chunk_error}"
            )
            partial_errors.append(chunk_summary)
            skipped_chunks.append(
                {
                    "chunk_index": chunk_index,
                    "chunks_total": total_chunks,
                    "date_from": common_begin.isoformat(),
                    "date_to": common_end.isoformat(),
                    "campaigns_count": len(ids_chunk),
                    "advert_ids": [int(advert_id) for advert_id in ids_chunk],
                    "error": chunk_error,
                }
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

                # Всегда сохраняем агрегат дня на уровне кампании, даже если WB отдал
                # детализацию по артикулам. Это сохраняет показы/клики/заказы и не
                # заставляет витрины восстанавливать их только из raw_payload.
                stat_rows_map[(int(advert_id), stat_date, 0)] = {
                    "spend": day_spend,
                    "day_sum": day_spend,
                    "views": day_views,
                    "clicks": day_clicks,
                    "orders": day_orders,
                    "add_to_cart": day_atc,
                    "raw_payload": _compact_fullstats_payload(campaign_row=campaign_row, day_row=day_row),
                    "updated_at": stats_now,
                }

                apps = day_row.get("apps") or []
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
                            stat_rows_map[(int(advert_id), stat_date, int(nm_id))] = {
                                "spend": nm_spend,
                                "day_sum": day_spend,
                                "views": None,
                                "clicks": None,
                                "orders": None,
                                "add_to_cart": None,
                                "raw_payload": _compact_fullstats_payload(
                                    campaign_row=campaign_row,
                                    day_row=day_row,
                                    app_row=app_row,
                                    nm_row=nm_row,
                                ),
                                "updated_at": stats_now,
                            }
        rows_upserted = _bulk_upsert_stats(stat_rows_map)
        stats_rows_upserted += rows_upserted
        if callable(on_progress):
            on_progress(
                {
                    "mode": "chunk_done",
                    "chunk_index": chunk_index,
                    "chunks_total": total_chunks,
                    "chunk_size": len(ids_chunk),
                    "date_from": common_begin,
                    "date_to": common_end,
                    "advert_ids": list(ids_chunk),
                    "rows_upserted": rows_upserted,
                    "stats_rows_upserted": stats_rows_upserted,
                }
            )

    result = {
        "campaigns_synced": campaigns_synced,
        "stats_rows_upserted": stats_rows_upserted,
    }
    if partial_errors:
        result["skipped_chunks_count"] = len(skipped_chunks)
        result["skipped_chunks"] = skipped_chunks[:20]
        result["error"] = (
            f"Часть статистики рекламы пропущена: {len(skipped_chunks)} "
            f"из {total_chunks} чанков. Последняя причина WB: {partial_errors[-1]}"
        )
    return result


def sync_active_paused_ad_campaigns_full_history(
    seller: SellerAccount,
    *,
    period_days: int = 30,
    on_progress=None,
    on_chunk_progress=None,
) -> Dict[str, int]:
    client = WBPromotionClient(seller.api_token_plain)
    campaigns_rows = client.list_adverts(statuses=[9, 11])
    if not isinstance(campaigns_rows, list) or not campaigns_rows:
        return {
            "campaigns_synced": 0,
            "stats_rows_upserted": 0,
            "periods_processed": 0,
            "periods_total": 0,
        }

    oldest_start: date | None = None
    for row in campaigns_rows:
        if not isinstance(row, dict):
            continue
        start_date = _extract_campaign_start_date(row)
        if start_date is None:
            continue
        oldest_start = start_date if oldest_start is None else min(oldest_start, start_date)

    today = timezone.localdate()
    if oldest_start is None:
        oldest_start = today

    periods: List[Tuple[date, date]] = []
    cursor = oldest_start
    chunk_days = max(1, int(period_days))
    while cursor <= today:
        period_end = min(cursor + timedelta(days=chunk_days - 1), today)
        periods.append((cursor, period_end))
        cursor = period_end + timedelta(days=1)

    total_campaigns_synced = 0
    total_stats_rows_upserted = 0
    collected_errors: List[str] = []
    fullstats_rate_state: Dict[str, float] = {}

    for idx, (period_start, period_end) in enumerate(periods, start=1):
        if callable(on_progress):
            on_progress(idx, len(periods), period_start, period_end)

        def handle_chunk_progress(payload: dict) -> None:
            if not callable(on_chunk_progress) or not isinstance(payload, dict):
                return
            enriched_payload = dict(payload)
            enriched_payload.update(
                {
                    "period_index": idx,
                    "periods_total": len(periods),
                    "period_start": period_start,
                    "period_end": period_end,
                }
            )
            on_chunk_progress(enriched_payload)

        result = sync_ad_campaigns_and_stats(
            seller=seller,
            date_from=period_start,
            date_to=period_end,
            campaign_statuses=[9, 11],
            on_progress=handle_chunk_progress,
            fullstats_rate_state=fullstats_rate_state,
        )
        total_campaigns_synced = max(total_campaigns_synced, int(result.get("campaigns_synced") or 0))
        total_stats_rows_upserted += int(result.get("stats_rows_upserted") or 0)
        if result.get("error"):
            collected_errors.append(str(result["error"]))

    payload = {
        "campaigns_synced": total_campaigns_synced,
        "stats_rows_upserted": total_stats_rows_upserted,
        "periods_processed": len(periods),
        "periods_total": len(periods),
        "date_from": oldest_start.isoformat(),
        "date_to": today.isoformat(),
    }
    if collected_errors:
        payload["error"] = collected_errors[-1]
    return payload
