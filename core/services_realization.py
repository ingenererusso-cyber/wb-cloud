from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
import re
from time import sleep
from typing import Dict
from typing import Iterable
from typing import List

from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.dateparse import parse_datetime

from core.models import Order
from core.models import Product
from core.models import RealizationReportDetail
from core.models import SellerAccount
from core.models import WbWarehouseTariff
from wb_api.client import WBStatisticsReportsClient


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


def sync_realization_report_detail(
    seller: SellerAccount,
    date_from: date,
    date_to: date,
    period: str = "weekly",
    limit: int = 100000,
    respect_rate_limit: bool = True,
) -> dict:
    """
    Синхронизирует WB reportDetailByPeriod в таблицу RealizationReportDetail.
    """
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")
    if period not in {"weekly", "daily"}:
        raise ValueError("period must be weekly or daily")

    client = WBStatisticsReportsClient(seller.api_token)
    date_from_str = date_from.isoformat()
    date_to_str = date_to.isoformat()

    total_upserted = 0
    pages = 0
    rrdid = 0

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
        for row in rows:
            rrd_id = _to_int(row.get("rrd_id"))
            if rrd_id is None:
                continue

            RealizationReportDetail.objects.update_or_create(
                seller=seller,
                rrd_id=rrd_id,
                defaults={
                    "realizationreport_id": _to_int(row.get("realizationreport_id")),
                    "date_from": _to_date(row.get("date_from")),
                    "date_to": _to_date(row.get("date_to")),
                    "create_dt": _to_date(row.get("create_dt")),
                    "srid": _normalize(row.get("srid")) or None,
                    "nm_id": _to_int(row.get("nm_id")),
                    "sa_name": _normalize(row.get("sa_name")) or None,
                    "office_name": _normalize(row.get("office_name")) or None,
                    "site_country": _normalize(row.get("site_country")) or None,
                    "bonus_type_name": _normalize(row.get("bonus_type_name")) or None,
                    "supplier_oper_name": _normalize(row.get("supplier_oper_name")) or None,
                    "doc_type_name": _normalize(row.get("doc_type_name")) or None,
                    "order_dt": _to_datetime(row.get("order_dt")),
                    "sale_dt": _to_datetime(row.get("sale_dt")),
                    "rr_dt": _to_date(row.get("rr_dt")),
                    "fix_tariff_date_from": _to_date(row.get("fix_tariff_date_from")),
                    "fix_tariff_date_to": _to_date(row.get("fix_tariff_date_to")),
                    "quantity": _to_int(row.get("quantity")),
                    "delivery_rub": _to_float(row.get("delivery_rub")),
                    "dlv_prc": _to_float(row.get("dlv_prc")),
                    "storage_fee": _to_float(row.get("storage_fee")),
                    "deduction": _to_float(row.get("deduction")),
                    "acceptance": _to_float(row.get("acceptance")),
                    "rebill_logistic_cost": _to_float(row.get("rebill_logistic_cost")),
                    "raw_payload": row,
                },
            )
            total_upserted += 1

        last_rrd_id = _to_int(rows[-1].get("rrd_id"))
        if last_rrd_id is None or len(rows) < limit:
            break
        rrdid = last_rrd_id

        # WB limit: 1 request per minute.
        if respect_rate_limit:
            sleep(61)

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


def _calculate_box_logistics_per_unit(base: float, liter: float, volume_liters: float) -> float:
    """
    Базовая стоимость логистики для 1 единицы товара по объему (без коэффициента):
    - 0.001-0.200 л: 23 ₽
    - 0.201-0.400 л: 26 ₽
    - 0.401-0.600 л: 29 ₽
    - 0.601-0.800 л: 30 ₽
    - 0.801-1.000 л: 32 ₽
    - >1.000 л: 46 + 14 * (объем - 1)

    Параметры base/liter оставлены в сигнатуре для совместимости вызовов и
    в текущей модели не используются.
    """
    volume = max(float(volume_liters or 0.0), 0.0)
    if volume <= 0:
        return 0.0
    if volume <= 0.2:
        return 23.0
    if volume <= 0.4:
        return 26.0
    if volume <= 0.6:
        return 29.0
    if volume <= 0.8:
        return 30.0
    if volume <= 1.0:
        return 32.0
    return 46.0 + 14.0 * (volume - 1.0)


def _resolve_delivery_multiplier(
    api_coef_expr: float | None,
    fixed_delivery_coef: float | None,
) -> float:
    """
    Возвращает множитель доставки:
    - приоритет fixed_delivery_coef (dlv_prc), если > 0
    - иначе api_coef_expr / 100, если > 0
    - иначе 1.0
    """
    dlv_prc = _to_float(fixed_delivery_coef)
    if dlv_prc is not None and dlv_prc > 0:
        return float(dlv_prc)

    coef_expr = _to_float(api_coef_expr)
    if coef_expr is not None and coef_expr > 0:
        return float(coef_expr) / 100.0

    return 1.0


def _apply_fixed_delivery_coef(
    base: float,
    liter: float,
    api_coef_expr: float | None,
    fixed_delivery_coef: float | None,
) -> tuple[float, float]:
    """
    Применяет фиксированный коэффициент поставки (dlv_prc), если он есть.

    Логика:
    - тарифы в БД сохранены уже с коэффициентом из API (coef_expr, %)
    - если для строки есть dlv_prc, заменяем коэффициент API на dlv_prc
      через пересчет:
      base'  = base  * (dlv_prc / (coef_expr / 100))
      liter' = liter * (dlv_prc / (coef_expr / 100))
    """
    dlv_prc = _to_float(fixed_delivery_coef)
    if dlv_prc is None or dlv_prc <= 0:
        return (base, liter)

    coef_expr = _to_float(api_coef_expr)
    if coef_expr is None or coef_expr <= 0:
        return (base * dlv_prc, liter * dlv_prc)

    api_multiplier = coef_expr / 100.0
    if api_multiplier <= 0:
        return (base * dlv_prc, liter * dlv_prc)

    factor = dlv_prc / api_multiplier
    return (base * factor, liter * factor)


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
    theoretical_sum = 0.0
    rows_without_tariff = 0
    rows_without_cost_parts = 0
    rows_without_volume = 0
    rows_excluded_mp = 0

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
            base = tariff.box_delivery_marketplace_base
            liter = tariff.box_delivery_marketplace_liter
            coef_expr = tariff.box_delivery_marketplace_coef_expr
        else:
            coef_expr = tariff.box_delivery_coef_expr
        base = float(base or 0.0)
        liter = float(liter or 0.0)
        delivery_multiplier = _resolve_delivery_multiplier(
            api_coef_expr=coef_expr,
            fixed_delivery_coef=row.dlv_prc,
        )
        if delivery_multiplier <= 0:
            rows_without_cost_parts += 1
            continue

        srid = _normalize(row.srid)
        article = _normalize(row.sa_name) or article_by_srid_norm.get(_normalize_key(srid), "")
        article_key = _normalize_key(article)
        if not article_key or article_key not in volume_map:
            rows_without_volume += 1
            continue
        volume = max(float(volume_map[article_key]), 0.0)
        if volume <= 0:
            rows_without_volume += 1
            continue
        baseline_per_unit = _calculate_box_logistics_per_unit(
            base=base,
            liter=liter,
            volume_liters=volume,
        )
        if baseline_per_unit <= 0:
            rows_without_cost_parts += 1
            continue

        theoretical_per_unit = baseline_per_unit * delivery_multiplier
        # Для расчета ИЛ считаем по единице строки отчета:
        # quantity в детализации WB для логистики часто 0/служебное и не
        # отражает фактический множитель услуги доставки.
        theoretical = theoretical_per_unit
        actual = float(row.delivery_rub or 0.0)

        theoretical_sum += theoretical
        actual_sum += actual

    fact_index = (actual_sum / theoretical_sum) if theoretical_sum > 0 else None
    return {
        "rows_considered": len(rows),
        "actual_logistics_sum": round(actual_sum, 2),
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
