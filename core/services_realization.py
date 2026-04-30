from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
import re
from time import monotonic, sleep
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List

from django.db.models import Count
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.dateparse import parse_datetime

from core.logistics import (
    DEFAULT_LOGISTICS_VOLUME_LITERS,
    calculate_theoretical_order_logistics,
    get_krp_for_share,
    LOGISTICS_IRP_SWITCH_DATE,
)
from core.models import Order
from core.models import Product
from core.models import RealizationReportDetail
from core.models import SellerAccount
from core.models import WbWarehouseTariff
from wb_api.client import WBFinanceReportsClient

SQL_IN_CHUNK_SIZE = 10_000


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().replace(" ", "").replace(",", ".")
        if not normalized or normalized in {"-", "—", "None", "null"}:
            return None
        value = normalized
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _to_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    return parse_date(str(value))


def _to_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif value:
        dt = parse_datetime(str(value))
    else:
        dt = None
    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _normalize(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_key(value) -> str:
    return _normalize(value).lower()


def _row_get(row: dict, *keys):
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _is_return_row(row: dict) -> bool:
    doc_type = _normalize(_row_get(row, "doc_type_name", "docTypeName")).lower()
    supplier_oper = _normalize(_row_get(row, "supplier_oper_name", "supplierOperName", "sellerOperName")).lower()
    bonus_type = _normalize(_row_get(row, "bonus_type_name", "bonusTypeName")).lower()
    return_amount = _to_float(_row_get(row, "returnAmount", "return_amount"))
    if return_amount is not None and return_amount > 0:
        return True
    return (
        "возврат" in doc_type
        or "возврат" in supplier_oper
        or "возврат" in bonus_type
        or "отмен" in doc_type
        or "отмен" in supplier_oper
        or "отмен" in bonus_type
    )


def _build_realization_defaults(row: dict) -> dict:
    return {
        "realizationreport_id": _to_int(_row_get(row, "realizationreport_id", "realizationReportId")),
        "date_from": _to_date(_row_get(row, "date_from", "dateFrom")),
        "date_to": _to_date(_row_get(row, "date_to", "dateTo")),
        "create_dt": _to_date(_row_get(row, "create_dt", "createDt", "createDate")),
        "srid": _normalize(_row_get(row, "srid")) or None,
        "nm_id": _to_int(_row_get(row, "nm_id", "nmId")),
        "sa_name": _normalize(_row_get(row, "sa_name", "vendorCode")) or None,
        "office_name": _normalize(_row_get(row, "office_name", "warehouseName", "officeName")) or None,
        "site_country": _normalize(_row_get(row, "site_country", "countryName", "country")) or None,
        "bonus_type_name": _normalize(_row_get(row, "bonus_type_name", "bonusTypeName")) or None,
        "supplier_oper_name": _normalize(_row_get(row, "supplier_oper_name", "supplierOperName", "sellerOperName")) or None,
        "doc_type_name": _normalize(_row_get(row, "doc_type_name", "docTypeName")) or None,
        "order_dt": _to_datetime(_row_get(row, "order_dt", "orderDt")),
        "sale_dt": _to_datetime(_row_get(row, "sale_dt", "saleDt")),
        "rr_dt": _to_date(_row_get(row, "rr_dt", "rrDt", "rrDate")),
        "fix_tariff_date_from": _to_date(_row_get(row, "fix_tariff_date_from", "fixTariffDateFrom")),
        "fix_tariff_date_to": _to_date(_row_get(row, "fix_tariff_date_to", "fixTariffDateTo")),
        "quantity": _to_int(_row_get(row, "quantity")),
        "delivery_rub": _to_float(_row_get(row, "delivery_rub", "deliveryRub", "deliveryService")),
        "dlv_prc": _to_float(_row_get(row, "dlv_prc", "deliveryCoef")),
        "storage_fee": _to_float(_row_get(row, "storage_fee", "storageFee", "paidStorage")),
        "deduction": _to_float(_row_get(row, "deduction")),
        "acceptance": _to_float(_row_get(row, "acceptance", "paidAcceptance")),
        "rebill_logistic_cost": _to_float(_row_get(row, "rebill_logistic_cost", "rebillLogisticCost")),
        "raw_payload": row,
    }


def _iter_chunks(items: list, size: int):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def sync_realization_report_detail(
    seller: SellerAccount,
    date_from: date,
    date_to: date,
    period: str = "weekly",
    limit: int = 100000,
    respect_rate_limit: bool = True,
    on_heartbeat: Callable[[str], None] | None = None,
    heartbeat_interval_seconds: float = 20.0,
) -> dict:
    """
    Синхронизирует WB reportDetailByPeriod в таблицу RealizationReportDetail.
    """
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")
    if period not in {"weekly", "daily"}:
        raise ValueError("period must be weekly or daily")

    client = WBFinanceReportsClient(seller.api_token_plain)
    date_from_str = date_from.isoformat()
    date_to_str = date_to.isoformat()

    total_upserted = 0
    pages = 0
    rrdid = 0
    return_srids: set[str] = set()
    last_heartbeat_ts = monotonic()
    update_fields = [
        "realizationreport_id",
        "date_from",
        "date_to",
        "create_dt",
        "srid",
        "nm_id",
        "sa_name",
        "office_name",
        "site_country",
        "bonus_type_name",
        "supplier_oper_name",
        "doc_type_name",
        "order_dt",
        "sale_dt",
        "rr_dt",
        "fix_tariff_date_from",
        "fix_tariff_date_to",
        "quantity",
        "delivery_rub",
        "dlv_prc",
        "storage_fee",
        "deduction",
        "acceptance",
        "rebill_logistic_cost",
        "raw_payload",
    ]

    def _heartbeat(message: str, *, force: bool = False) -> None:
        nonlocal last_heartbeat_ts
        if not on_heartbeat:
            return
        now_ts = monotonic()
        if not force and (now_ts - last_heartbeat_ts) < heartbeat_interval_seconds:
            return
        on_heartbeat(message)
        last_heartbeat_ts = now_ts

    while True:
        status, rows = client.get_report_detail_by_period(
            date_from=date_from_str,
            date_to=date_to_str,
            limit=limit,
            rrdid=rrdid,
            period=period,
        )
        if status == 204 or not rows:
            break

        pages += 1
        _heartbeat(f"Отчёты реализации: получена страница {pages}, строк {len(rows)}.")
        page_items: list[tuple[int, dict]] = []
        for row in rows:
            rrd_id = _to_int(_row_get(row, "rrd_id", "rrdId"))
            if rrd_id is None:
                continue

            row_srid = _normalize(_row_get(row, "srid"))
            if row_srid and _is_return_row(row):
                return_srids.add(row_srid)

            page_items.append((rrd_id, _build_realization_defaults(row)))

        if page_items:
            page_rrd_ids = [item[0] for item in page_items]
            existing_map: dict[int, RealizationReportDetail] = {}
            for rrd_chunk in _iter_chunks(page_rrd_ids, SQL_IN_CHUNK_SIZE):
                for item in RealizationReportDetail.objects.filter(
                    seller=seller,
                    rrd_id__in=rrd_chunk,
                ):
                    existing_map[item.rrd_id] = item
            to_create: list[RealizationReportDetail] = []
            to_update: list[RealizationReportDetail] = []
            for row_rrd_id, defaults in page_items:
                existing = existing_map.get(row_rrd_id)
                if existing is None:
                    to_create.append(
                        RealizationReportDetail(
                            seller=seller,
                            rrd_id=row_rrd_id,
                            **defaults,
                        )
                    )
                    continue
                for field_name, field_value in defaults.items():
                    setattr(existing, field_name, field_value)
                to_update.append(existing)

            if to_create:
                for create_chunk in _iter_chunks(to_create, SQL_IN_CHUNK_SIZE):
                    try:
                        RealizationReportDetail.objects.bulk_create(create_chunk, batch_size=2000)
                    except Exception as exc:
                        raise RuntimeError(
                            "Ошибка записи новых строк отчёта реализации "
                            f"(chunk={len(create_chunk)}, pages={pages}, upserted={total_upserted}): {exc}"
                        ) from exc
                    _heartbeat(
                        "Отчёты реализации: запись новых строк "
                        f"({len(create_chunk)} шт., всего обработано {total_upserted + len(page_items)})."
                    )
            if to_update:
                for update_chunk in _iter_chunks(to_update, SQL_IN_CHUNK_SIZE):
                    try:
                        RealizationReportDetail.objects.bulk_update(update_chunk, update_fields, batch_size=2000)
                    except Exception as exc:
                        raise RuntimeError(
                            "Ошибка обновления строк отчёта реализации "
                            f"(chunk={len(update_chunk)}, pages={pages}, upserted={total_upserted}): {exc}"
                        ) from exc
                    _heartbeat(
                        "Отчёты реализации: обновление строк "
                        f"({len(update_chunk)} шт., всего обработано {total_upserted + len(page_items)})."
                    )

            total_upserted += len(page_items)
            _heartbeat(
                f"Отчёты реализации: обработано {total_upserted} строк, страниц {pages}.",
                force=True,
            )

        last_rrd_id = _to_int(_row_get(rows[-1], "rrd_id", "rrdId"))
        if last_rrd_id is None or len(rows) < limit:
            break
        rrdid = last_rrd_id

        # WB limit: 1 request per minute.
        if respect_rate_limit:
            sleep(61)

    if return_srids:
        return_srid_list = list(return_srids)
        for srid_chunk in _iter_chunks(return_srid_list, SQL_IN_CHUNK_SIZE):
            try:
                Order.objects.filter(
                    seller=seller,
                    srid__in=srid_chunk,
                ).update(is_return=True, is_buyout=False)
            except Exception as exc:
                raise RuntimeError(
                    "Ошибка обновления возвратов по SRID "
                    f"(chunk={len(srid_chunk)}, return_srids={len(return_srid_list)}): {exc}"
                ) from exc
            _heartbeat(
                "Отчёты реализации: помечены возвраты "
                f"({len(srid_chunk)} SRID)."
            )

    return {
        "pages": pages,
        "upserted_rows": total_upserted,
        "date_from": date_from_str,
        "date_to": date_to_str,
        "period": period,
    }


def _pick_tariff_for_date(tariffs: List[WbWarehouseTariff], target_date: date | None) -> WbWarehouseTariff | None:
    if not tariffs:
        return None
    if target_date is None:
        return tariffs[0]
    eligible = [t for t in tariffs if t.tariff_date <= target_date]
    if eligible:
        return eligible[0]
    return tariffs[0]


def _strip_mp_tokens(office_name: str) -> str:
    tokens = [t for t in re.split(r"\s+", office_name.strip()) if t]
    tokens = [t for t in tokens if t.lower() != "мп"]
    return " ".join(tokens).strip()


def _is_mp_office(office_name: str | None) -> bool:
    office_key = _normalize_key(office_name)
    return " мп " in f" {office_key} " or office_key.endswith(" мп") or office_key.startswith("мп ")


def _resolve_tariff_for_realization_row(
    row: RealizationReportDetail,
    tariffs_by_warehouse: Dict[str, List[WbWarehouseTariff]],
) -> WbWarehouseTariff | None:
    office_name = _normalize(row.office_name)
    target_date = row.fix_tariff_date_from or row.rr_dt

    # Default path: office_name == warehouse_name
    direct = _pick_tariff_for_date(tariffs_by_warehouse.get(office_name, []), target_date)

    is_mp_office = _is_mp_office(office_name)
    if not is_mp_office:
        return direct

    # For seller-warehouse (МП) rows use marketplace tariff for region.
    base_warehouse_name = _strip_mp_tokens(office_name)
    base_tariff = _pick_tariff_for_date(tariffs_by_warehouse.get(base_warehouse_name, []), target_date)

    region_name = _normalize(base_tariff.geo_name if base_tariff else None)
    if not region_name:
        region_name = _normalize(row.site_country)
    if not region_name:
        return direct

    marketplace_warehouse_name = f"Маркетплейс: {region_name}"
    marketplace_tariff = _pick_tariff_for_date(
        tariffs_by_warehouse.get(marketplace_warehouse_name, []),
        target_date,
    )
    return marketplace_tariff or direct


def _calculate_irp_index_for_effective_date(
    seller: SellerAccount,
    effective_date: date,
) -> float:
    """
    ИРП рассчитывается по окну последних 13 недель, предшествующих дате применения:
    - обновление в ночь с воскресенья на понедельник,
    - для заказов текущей недели действует значение, рассчитанное по предыдущим 13 неделям.
    """
    if effective_date < LOGISTICS_IRP_SWITCH_DATE:
        return 0.0

    week_start = effective_date - timedelta(days=effective_date.weekday())  # понедельник
    window_end = week_start - timedelta(days=1)  # предыдущее воскресенье
    window_start = window_end - timedelta(days=13 * 7 - 1)

    article_rows = (
        Order.objects
        .filter(
            seller=seller,
            is_cancel=False,
            is_return=False,
            warehouse_type="Склад WB",
            country_name="Россия",
            order_date__date__gte=window_start,
            order_date__date__lte=window_end,
        )
        .values("supplier_article")
        .annotate(
            orders_total=Count("id"),
            orders_local=Count("id", filter=Q(is_local=True)),
        )
    )

    rows = list(article_rows)
    total_orders = sum(int(r["orders_total"] or 0) for r in rows)
    if total_orders <= 0:
        return 0.0

    weighted_sum = 0.0
    for row in rows:
        orders_total = int(row["orders_total"] or 0)
        if orders_total <= 0:
            continue
        orders_local = int(row["orders_local"] or 0)
        local_share = (orders_local / orders_total) * 100.0
        krp = get_krp_for_share(local_share, as_of_date=effective_date)
        weighted_sum += orders_total * krp

    irp_index = weighted_sum / total_orders
    return round(float(irp_index), 6)


def calculate_fact_vs_theory_localization_index(
    seller: SellerAccount,
    date_from: date,
    date_to: date,
    include_only_to_client_logistics: bool = True,
    include_only_sale_rows: bool = True,
) -> dict:
    """
    Сравнивает фактическую логистику из отчета реализации с теоретической.
    """
    rows_qs = RealizationReportDetail.objects.filter(
        seller=seller,
        rr_dt__gte=date_from,
        rr_dt__lte=date_to,
    )
    if include_only_to_client_logistics:
        rows_qs = rows_qs.filter(bonus_type_name__icontains="К клиенту")
    if include_only_sale_rows:
        rows_qs = rows_qs.filter(bonus_type_name__icontains="при продаже")
    rows_qs = rows_qs.exclude(delivery_rub__isnull=True).exclude(delivery_rub__lte=0)
    rows = list(rows_qs)

    if not rows:
        return {
            "rows_considered": 0,
            "actual_logistics_sum": 0.0,
            "actual_raw_logistics_sum": 0.0,
            "actual_adjusted_logistics_sum": 0.0,
            "irp_component_sum": 0.0,
            "theoretical_logistics_sum": 0.0,
            "fact_index": None,
            "rows_without_tariff": 0,
            "rows_without_cost_parts": 0,
            "rows_without_volume": 0,
            "rows_excluded_mp": 0,
        }

    srids = [_normalize(r.srid) for r in rows if _normalize(r.srid)]
    article_by_srid: Dict[str, str] = dict(
        Order.objects.filter(seller=seller, srid__in=srids).values_list("srid", "supplier_article")
    )
    article_by_srid_norm: Dict[str, str] = {_normalize_key(k): v for k, v in article_by_srid.items()}
    finished_price_by_srid_norm: Dict[str, float] = {}
    for srid_value, finished_price in (
        Order.objects
        .filter(seller=seller, srid__in=srids)
        .exclude(finished_price__isnull=True)
        .values_list("srid", "finished_price")
    ):
        key = _normalize_key(srid_value)
        if not key:
            continue
        parsed_price = _to_float(finished_price)
        if parsed_price is None:
            continue
        # На случай дублей по srid берем максимальную цену как более консервативную.
        finished_price_by_srid_norm[key] = max(
            float(parsed_price),
            finished_price_by_srid_norm.get(key, 0.0),
        )
    volume_map: Dict[str, float] = {}
    for item in Product.objects.filter(seller=seller).exclude(vendor_code__isnull=True).exclude(vendor_code="").values(
        "vendor_code",
        "volume_liters",
    ):
        article = _normalize(item["vendor_code"])
        if not article:
            continue
        key = _normalize_key(article)
        volume_map[key] = max(float(item["volume_liters"] or 0.0), volume_map.get(key, 0.0))

    tariff_qs = WbWarehouseTariff.objects.filter(seller=seller).order_by(
        "warehouse_name",
        "-tariff_date",
    )
    tariffs_by_warehouse: Dict[str, List[WbWarehouseTariff]] = {}
    for t in tariff_qs.iterator(chunk_size=1000):
        tariffs_by_warehouse.setdefault(t.warehouse_name, []).append(t)

    actual_sum = 0.0
    actual_raw_sum = 0.0
    actual_adjusted_sum = 0.0
    irp_component_sum = 0.0
    theoretical_sum = 0.0
    rows_without_tariff = 0
    rows_without_cost_parts = 0
    rows_without_volume = 0
    rows_excluded_mp = 0
    irp_cache: Dict[date, float] = {}

    for row in rows:
        if _is_mp_office(row.office_name):
            rows_excluded_mp += 1
            continue

        tariff = _resolve_tariff_for_realization_row(
            row=row,
            tariffs_by_warehouse=tariffs_by_warehouse,
        )
        if tariff is None:
            rows_without_tariff += 1
            continue

        base = tariff.box_delivery_base
        liter = tariff.box_delivery_liter
        if base is None and liter is None:
            coef_expr = tariff.box_delivery_marketplace_coef_expr
        else:
            coef_expr = tariff.box_delivery_coef_expr

        srid = _normalize(row.srid)
        article = _normalize(row.sa_name) or article_by_srid_norm.get(_normalize_key(srid), "")
        article_key = _normalize_key(article)
        volume = volume_map.get(article_key)
        if volume is None or float(volume) <= 0:
            rows_without_volume += 1
            volume = DEFAULT_LOGISTICS_VOLUME_LITERS

        effective_date = row.rr_dt or row.fix_tariff_date_from or date_to
        irp_index = irp_cache.get(effective_date)
        if irp_index is None:
            irp_index = _calculate_irp_index_for_effective_date(
                seller=seller,
                effective_date=effective_date,
            )
            irp_cache[effective_date] = irp_index
        retail_price = (
            finished_price_by_srid_norm.get(_normalize_key(srid))
            or _to_float((row.raw_payload or {}).get("retail_price"))
            or 0.0
        )

        theoretical_per_unit = calculate_theoretical_order_logistics(
            volume_liters=float(volume),
            api_coef_expr=coef_expr,
            fixed_delivery_coef=row.dlv_prc,
            use_dlv_prc=True,
            default_volume_liters=DEFAULT_LOGISTICS_VOLUME_LITERS,
        )
        if theoretical_per_unit <= 0:
            rows_without_cost_parts += 1
            continue

        # Для расчета ИЛ считаем по единице строки отчета:
        # quantity в детализации WB для логистики часто 0/служебное и не
        # отражает фактический множитель услуги доставки.
        theoretical = theoretical_per_unit
        actual_raw = float(row.delivery_rub or 0.0)
        irp_component = 0.0
        if effective_date >= LOGISTICS_IRP_SWITCH_DATE:
            irp_component = max(float(retail_price or 0.0), 0.0) * max(float(irp_index or 0.0), 0.0)
        actual_adjusted = actual_raw - irp_component if effective_date >= LOGISTICS_IRP_SWITCH_DATE else actual_raw
        actual = actual_adjusted

        theoretical_sum += theoretical
        actual_sum += actual
        actual_raw_sum += actual_raw
        actual_adjusted_sum += actual_adjusted
        irp_component_sum += irp_component

    fact_index = (actual_sum / theoretical_sum) if theoretical_sum > 0 else None
    return {
        "rows_considered": len(rows),
        "actual_logistics_sum": round(actual_sum, 2),
        "actual_raw_logistics_sum": round(actual_raw_sum, 2),
        "actual_adjusted_logistics_sum": round(actual_adjusted_sum, 2),
        "irp_component_sum": round(irp_component_sum, 2),
        "theoretical_logistics_sum": round(theoretical_sum, 2),
        "fact_index": round(fact_index, 6) if fact_index is not None else None,
        "rows_without_tariff": rows_without_tariff,
        "rows_without_cost_parts": rows_without_cost_parts,
        "rows_without_volume": rows_without_volume,
        "rows_excluded_mp": rows_excluded_mp,
    }


def get_fact_localization_index_trend_last_full_weeks(
    seller: SellerAccount,
    weeks: int = 25,
) -> dict:
    """
    Тренд фактического индекса локализации по полным неделям.

    Для каждой недели учитываются только выкупленные заказы (логистика
    "К клиенту при продаже"). Если за неделю нет ни одной строки, точка
    не добавляется в график.
    """
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    last_full_week_end = current_week_start - timedelta(days=1)

    points = []
    for offset in range(weeks - 1, -1, -1):
        week_end = last_full_week_end - timedelta(days=offset * 7)
        week_start = week_end - timedelta(days=6)

        result = calculate_fact_vs_theory_localization_index(
            seller=seller,
            date_from=week_start,
            date_to=week_end,
            include_only_to_client_logistics=True,
            include_only_sale_rows=True,
        )
        if result["rows_considered"] <= 0 or result["fact_index"] is None:
            continue

        points.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "label": f"{week_start.strftime('%d.%m')}-{week_end.strftime('%d.%m')}",
                "fact_index": float(result["fact_index"]),
                "rows_considered": int(result["rows_considered"]),
                "actual_logistics_sum": float(result["actual_logistics_sum"]),
                "actual_raw_logistics_sum": float(result.get("actual_raw_logistics_sum", result["actual_logistics_sum"])),
                "actual_adjusted_logistics_sum": float(result.get("actual_adjusted_logistics_sum", result["actual_logistics_sum"])),
                "irp_component_sum": float(result.get("irp_component_sum", 0.0)),
                "theoretical_logistics_sum": float(result["theoretical_logistics_sum"]),
            }
        )

    return {
        "start_date": points[0]["week_start"] if points else None,
        "end_date": points[-1]["week_end"] if points else None,
        "start_label": points[0]["label"].split("-")[0] if points else None,
        "end_label": points[-1]["label"].split("-")[1] if points else None,
        "points": points,
    }
