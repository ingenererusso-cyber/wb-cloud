from datetime import date, datetime, time as dt_time, timedelta
import json
import sqlite3
import threading
import traceback
import uuid

from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate, TruncHour
from django.db import OperationalError, close_old_connections
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
import time
from app.services.supply_recommendations.loaders import list_available_transit_warehouses, list_regular_warehouses
from app.services.supply_recommendations.service import get_dashboard_supply_recommendations
from core.models import (
    AppErrorLog,
    Order,
    Product,
    ProductSizePrice,
    RealizationReportDetail,
    SellerAccount,
    SellerFbsStock,
    ProductCardSize,
    ProductUnitEconomicsCalculation,
    SellerWarehouse,
    SupportMessage,
    SupportThread,
    SupportThreadParticipantState,
    SyncTask,
    TesterFeedback,
    TransitDirectionTariff,
    UnitEconomicsSettings,
    WarehouseStockDetailed,
    WbAcceptanceCoefficient,
    WbAdvertCampaign,
    WbAdvertStatDaily,
    WbCategoryCommission,
    WbWarehouseTariff,
)
from core.logistics import (
    DEFAULT_LOGISTICS_VOLUME_LITERS,
    LOGISTICS_IRP_SWITCH_DATE,
    calculate_box_logistics_base_by_volume,
    calculate_theoretical_order_logistics,
)
from core.services.replenishment import calculate_replenishment
from core.services_realization import (
    get_fact_localization_index_trend_last_full_weeks,
    sync_realization_report_detail,
)
from core.services_advertising import sync_ad_campaigns_and_stats
from core.services_offices import sync_wb_offices
from core.services_orders import sync_fbw_orders, sync_sales_buyout_flags
from core.services_products import sync_products_content
from core.services_prices import sync_product_size_prices
from core.services_commissions import sync_category_commissions
from core.services_stocks import sync_supplier_stocks
from core.services_seller_warehouses import sync_seller_warehouses
from core.services_fbs_stocks import sync_seller_fbs_stocks
from core.services_fbs_stocks import apply_fbs_stock_updates
from core.services_tariffs import (
    sync_acceptance_coefficients,
    sync_transit_direction_tariffs,
    sync_warehouse_tariffs,
)
from core.services.localization import (
    get_local_orders_percent_last_full_week,
    get_local_orders_percent_trend_last_full_weeks,
    get_theoretical_irp_trend_last_full_weeks,
    get_theoretical_localization_index_trend_last_full_weeks,
    get_top_non_local_districts_last_full_weeks,
)

SYNC_TASK_STALE_MINUTES = 8
SYNC_STAGE_TTL_HOURS = 24
DAILY_SYNC_STAGES = {
    "products",
    "commissions",
    "tariffs",
    "acceptance",
    "offices",
    "seller_warehouses",
    "transit",
}
UNIT_MODEL_FBO = "fbo"
UNIT_MODEL_FBS = "fbs"
UNIT_MODEL_TYPES = {UNIT_MODEL_FBO, UNIT_MODEL_FBS}
INITIAL_SYNC_WEEKS = 25
INITIAL_SYNC_DAYS = INITIAL_SYNC_WEEKS * 7


def _get_or_create_unit_economics_settings(seller: SellerAccount) -> UnitEconomicsSettings:
    settings_obj, _ = UnitEconomicsSettings.objects.get_or_create(seller=seller)
    return settings_obj


def _to_float_or_default(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_percent(value, default: float = 0.0) -> float:
    return max(0.0, min(100.0, _to_float_or_default(value, default)))


def _normalize_unit_model_type(raw_value: str | None) -> str:
    value = (raw_value or "").strip().lower()
    if value in UNIT_MODEL_TYPES:
        return value
    return UNIT_MODEL_FBO


def _resolve_model_fulfillment_cost(
    settings_obj: UnitEconomicsSettings,
    model_type: str,
) -> float:
    legacy = max(0.0, _to_float_or_default(settings_obj.fulfillment_cost_per_order, 0.0))
    if model_type == UNIT_MODEL_FBS:
        direct = _to_float_or_default(getattr(settings_obj, "fbs_fulfillment_cost_per_order", None), legacy)
        return max(0.0, direct if direct > 0 else legacy)
    direct = _to_float_or_default(getattr(settings_obj, "fbo_fulfillment_cost_per_order", None), legacy)
    return max(0.0, direct if direct > 0 else legacy)


def _resolve_model_commission_percent(
    *,
    seller: SellerAccount,
    product: Product,
    model_type: str,
) -> float:
    if product.subject_id is None:
        return 0.0
    commission_row = (
        WbCategoryCommission.objects
        .filter(seller=seller, locale="ru", subject_id=product.subject_id)
        .order_by("-updated_at", "-id")
        .first()
    )
    if not commission_row:
        return 0.0
    if model_type == UNIT_MODEL_FBS:
        return round(_to_float_or_default(commission_row.kgvp_marketplace, 0.0), 2)
    return round(_to_float_or_default(commission_row.paid_storage_kgvp, 0.0), 2)


def _unit_model_labels(model_type: str) -> dict[str, str]:
    if model_type == UNIT_MODEL_FBS:
        return {
            "model_badge": "FBS",
            "delivery": "Логистика FBS",
            "non_buyout": "Логистика невыкупов FBS",
            "storage": "Хранение WB",
            "fulfillment": "Фулфилмент продавца",
            "acceptance": "Приемка/обработка FBS",
        }
    return {
        "model_badge": "FBO",
        "delivery": "Логистика FBO",
        "non_buyout": "Логистика невыкупов FBO",
        "storage": "Хранение WB",
        "fulfillment": "Фулфилмент продавца",
        "acceptance": "Приемка WB",
    }


def _extract_wb_for_pay_from_raw(payload: dict | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    for key in ("ppvz_for_pay", "ppvzForPay", "for_pay", "forPay"):
        if key in payload:
            return _to_float_or_default(payload.get(key), 0.0)
    return 0.0


def _extract_acquiring_fee_from_raw(payload: dict | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    for key in ("acquiring_fee", "acquiringFee"):
        if key in payload:
            return _to_float_or_default(payload.get(key), 0.0)
    return 0.0


def _extract_retail_price_withdisc_from_raw(payload: dict | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    for key in ("retail_price_withdisc_rub", "retailPriceWithdiscRub", "retailPriceWithDisc"):
        if key in payload:
            return _to_float_or_default(payload.get(key), 0.0)
    return 0.0


def _extract_retail_amount_from_raw(payload: dict | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    for key in ("retailAmount", "retail_amount"):
        if key in payload:
            return _to_float_or_default(payload.get(key), 0.0)
    return 0.0


def _extract_campaign_nm_ids_from_payload(payload: dict | None) -> list[int]:
    if not isinstance(payload, dict):
        return []
    nm_settings = payload.get("nm_settings")
    if not isinstance(nm_settings, list):
        return []
    result: list[int] = []
    for item in nm_settings:
        if not isinstance(item, dict):
            continue
        try:
            nm_id_int = int(item.get("nm_id"))
        except (TypeError, ValueError):
            continue
        if nm_id_int > 0:
            result.append(nm_id_int)
    # stable unique
    seen = set()
    unique: list[int] = []
    for value in result:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _is_buyout_rr_row(row: RealizationReportDetail, payload: dict | None) -> bool:
    if _is_cancel_or_return_rr_row(row, payload):
        return False
    row_for_pay = _extract_wb_for_pay_from_raw(payload)
    doc_type = (row.doc_type_name or "").strip().lower()
    bonus_type = (row.bonus_type_name or "").strip().lower()
    return ("продаж" in doc_type) or ("при продаже" in bonus_type) or (row_for_pay > 0)


def _is_cancel_or_return_rr_row(row: RealizationReportDetail, payload: dict | None) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    doc_type = (row.doc_type_name or "").strip().lower()
    bonus_type = (row.bonus_type_name or "").strip().lower()
    op_name = (row.supplier_oper_name or "").strip().lower()
    return_amount_raw = _to_float_or_default(
        payload.get("returnAmount", payload.get("return_amount")),
        0.0,
    )
    return (
        return_amount_raw > 0
        or ("возврат" in op_name)
        or ("возврат" in doc_type)
        or ("отмен" in op_name)
        or ("отмен" in doc_type)
        or ("отмен" in bonus_type)
        or ("сторно" in op_name)
        or ("сторно" in doc_type)
        or ("сторно" in bonus_type)
    )


def _build_sales_base_by_nm(
    *,
    seller: SellerAccount,
    date_from: date,
    date_to: date,
    nm_ids: set[int] | None = None,
) -> dict[int, float]:
    qs = RealizationReportDetail.objects.filter(
        seller=seller,
        rr_dt__gte=date_from,
        rr_dt__lte=date_to,
    )
    if nm_ids:
        qs = qs.filter(nm_id__in=list(nm_ids))

    sale_base_by_nm: dict[int, float] = {}
    seen_srids_by_nm: dict[int, set[str]] = {}
    for row in qs.iterator(chunk_size=2000):
        payload = row.raw_payload or {}
        if not _is_buyout_rr_row(row, payload):
            continue
        try:
            row_nm_id = int(row.nm_id or 0)
        except (TypeError, ValueError):
            row_nm_id = 0
        if row_nm_id <= 0:
            continue
        sale_price_withdisc = _extract_retail_price_withdisc_from_raw(payload)
        if sale_price_withdisc <= 0:
            continue

        row_srid = (row.srid or "").strip()
        if row_srid:
            seen = seen_srids_by_nm.setdefault(row_nm_id, set())
            if row_srid in seen:
                continue
            seen.add(row_srid)
        sale_base_by_nm[row_nm_id] = float(sale_base_by_nm.get(row_nm_id, 0.0) + sale_price_withdisc)
    return sale_base_by_nm


def _build_campaign_spend_totals(
    *,
    seller: SellerAccount,
    advert_ids: list[int],
    date_from: date,
    date_to: date,
) -> dict[int, float]:
    if not advert_ids:
        return {}
    rows = list(
        WbAdvertStatDaily.objects
        .filter(seller=seller, advert_id__in=advert_ids, stat_date__gte=date_from, stat_date__lte=date_to)
        .order_by("advert_id", "stat_date")
    )
    day_rollup: dict[tuple[int, date], dict] = {}
    for row in rows:
        try:
            advert_id_int = int(row.advert_id)
        except (TypeError, ValueError):
            continue
        if row.stat_date is None:
            continue
        key = (advert_id_int, row.stat_date)
        item = day_rollup.setdefault(key, {"day_sum": 0.0, "nm_sum": 0.0})
        item["nm_sum"] += float(row.spend or 0.0)
        payload = row.raw_payload or {}
        payload_day = payload.get("day") if isinstance(payload, dict) else None
        payload_day_sum = _to_float_or_default((payload_day or {}).get("sum"), 0.0) if isinstance(payload_day, dict) else 0.0
        if payload_day_sum > item["day_sum"]:
            item["day_sum"] = payload_day_sum

    totals: dict[int, float] = {}
    for (advert_id_int, _), values in day_rollup.items():
        amount = float(values["day_sum"] if values["day_sum"] > 0 else values["nm_sum"])
        totals[advert_id_int] = float(totals.get(advert_id_int, 0.0) + amount)
    return totals


def _allocate_campaign_spend_for_nm(
    *,
    target_nm_id: int,
    campaign_nm_ids: list[int],
    campaign_total_spend: float,
    sale_base_by_nm: dict[int, float],
) -> tuple[float, bool]:
    participants = [int(x) for x in campaign_nm_ids if int(x) > 0]
    if not participants or target_nm_id not in participants:
        return 0.0, False
    if len(participants) == 1:
        return float(campaign_total_spend), False

    total_sales = sum(float(sale_base_by_nm.get(nm, 0.0)) for nm in participants)
    if total_sales > 0:
        share = float(sale_base_by_nm.get(target_nm_id, 0.0)) / total_sales
        return float(campaign_total_spend) * share, True
    # если продаж по участникам нет, fallback на равное деление
    return float(campaign_total_spend) / float(len(participants)), True


def _extract_penalty_from_raw(payload: dict | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    for key in ("penalty", "fineAmount", "fine"):
        if key in payload:
            return _to_float_or_default(payload.get(key), 0.0)
    return 0.0


def _advert_type_label(advert_type: int | None) -> str:
    mapping = {
        8: "Аукцион",
        9: "Автоматическая",
    }
    if advert_type is None:
        return "-"
    return mapping.get(int(advert_type), str(advert_type))


def _advert_status_meta(status: int | None) -> tuple[str, str]:
    mapping = {
        9: ("Активна", "green"),
        11: ("Приостановлена", "yellow"),
        7: ("Завершена", "gray"),
        8: ("Отклонена", "red"),
        -1: ("Удаляется", "gray"),
    }
    if status is None:
        return "-", "gray"
    label, css = mapping.get(int(status), (str(status), "blue"))
    return label, css


def _build_fact_profit_metrics_for_product(
    *,
    seller: SellerAccount,
    nm_id: int,
    date_from: date,
    date_to: date,
    purchase_price: float,
    discounted_price: float,
    settings_obj: UnitEconomicsSettings,
) -> dict:
    insufficient_data_message = (
        "Еще недостаточно данных для расчета, требуется синхронизация данных "
        "или отсутствуют отчеты о выкупах"
    )
    rr_qs = RealizationReportDetail.objects.filter(
        seller=seller,
        nm_id=nm_id,
        rr_dt__gte=date_from,
        rr_dt__lte=date_to,
    )
    rr_rows = list(rr_qs)

    wb_transfer_sum = 0.0
    acquiring_sum = 0.0
    logistics_buyout_sum = 0.0
    logistics_cancel_return_sum = 0.0
    logistics_adjustments_sum = 0.0
    logistics_negative_sum = 0.0
    deduction_sum = 0.0
    jam_direct_sum = 0.0
    jam_sum = 0.0
    jam_is_approx = False
    penalty_sum = 0.0
    penalty_is_approx = False
    ad_spend_sum = 0.0
    ad_spend_is_approx = False
    acceptance_sum = 0.0
    buyout_srids: set[str] = set()
    buyout_rows_without_srid = 0
    buyout_sale_price_by_srid: dict[str, float] = {}
    buyout_sale_price_without_srid: list[float] = []
    buyout_retail_amount_by_srid: dict[str, float] = {}
    buyout_retail_amount_without_srid: list[float] = []
    buyout_spp_by_srid: dict[str, float] = {}
    buyout_spp_without_srid: list[float] = []
    cancel_return_srids: set[str] = set()
    cancel_return_rows_without_srid = 0
    row_cache: list[tuple[RealizationReportDetail, dict, bool, str, bool]] = []

    for row in rr_rows:
        payload = row.raw_payload or {}
        row_sale_price_withdisc = _extract_retail_price_withdisc_from_raw(payload)
        row_retail_amount = _extract_retail_amount_from_raw(payload)
        row_spp = _to_float_or_default(payload.get("spp", payload.get("SPP")), 0.0)
        row_has_spp = ("spp" in payload) or ("SPP" in payload)
        is_cancel_marker = _is_cancel_or_return_rr_row(row, payload)
        row_for_pay = _extract_wb_for_pay_from_raw(payload)
        doc_type = (row.doc_type_name or "").strip().lower()
        bonus_type = (row.bonus_type_name or "").strip().lower()
        is_buyout = (not is_cancel_marker) and (("продаж" in doc_type) or ("при продаже" in bonus_type) or (row_for_pay > 0))

        srid = (row.srid or "").strip()
        row_cache.append((row, payload, is_buyout, srid, is_cancel_marker))

        if is_cancel_marker:
            if srid:
                cancel_return_srids.add(srid)
            else:
                cancel_return_rows_without_srid += 1

    for row, _payload, is_buyout, srid, is_cancel_marker in row_cache:
        row_for_pay = _extract_wb_for_pay_from_raw(_payload)
        row_acquiring = _extract_acquiring_fee_from_raw(_payload)
        row_sale_price_withdisc = _extract_retail_price_withdisc_from_raw(_payload)
        row_retail_amount = _extract_retail_amount_from_raw(_payload)
        row_spp = _to_float_or_default(_payload.get("spp", _payload.get("SPP")), 0.0)
        row_has_spp = ("spp" in _payload) or ("SPP" in _payload)
        srid_marked_cancel_or_return = bool(srid and (srid in cancel_return_srids))
        effective_cancel_or_return = is_cancel_marker or srid_marked_cancel_or_return
        effective_buyout = is_buyout and not effective_cancel_or_return

        if not effective_cancel_or_return:
            wb_transfer_sum += row_for_pay
            acquiring_sum += max(row_acquiring, 0.0)

        if effective_buyout:
            if srid:
                if srid not in buyout_srids:
                    buyout_srids.add(srid)
                if row_sale_price_withdisc > 0 and srid not in buyout_sale_price_by_srid:
                    buyout_sale_price_by_srid[srid] = row_sale_price_withdisc
                if row_retail_amount > 0 and srid not in buyout_retail_amount_by_srid:
                    buyout_retail_amount_by_srid[srid] = row_retail_amount
                if row_has_spp and row_spp > 0 and srid not in buyout_spp_by_srid:
                    buyout_spp_by_srid[srid] = row_spp
            else:
                # Если srid в строке отчета пустой, считаем такую строку как 1 выкуп.
                buyout_rows_without_srid += 1
                if row_sale_price_withdisc > 0:
                    buyout_sale_price_without_srid.append(row_sale_price_withdisc)
                if row_retail_amount > 0:
                    buyout_retail_amount_without_srid.append(row_retail_amount)
                if row_has_spp and row_spp > 0:
                    buyout_spp_without_srid.append(row_spp)

        row_deduction = _to_float_or_default(row.deduction, 0.0)
        bonus_name = (row.bonus_type_name or "").strip().lower()
        if "джем" in bonus_name:
            jam_direct_sum += row_deduction
        else:
            deduction_sum += row_deduction
        acceptance_sum += _to_float_or_default(row.acceptance, 0.0)

        delivery_val = _to_float_or_default(row.delivery_rub, 0.0)
        rebill_val = _to_float_or_default(row.rebill_logistic_cost, 0.0)
        if delivery_val < 0:
            logistics_negative_sum += delivery_val
        if rebill_val < 0:
            logistics_negative_sum += rebill_val

        row_logistics_cost = max(delivery_val, 0.0) + max(rebill_val, 0.0)
        if row_logistics_cost <= 0:
            continue
        if effective_buyout:
            logistics_buyout_sum += row_logistics_cost
            continue
        # Для отмен/возвратов относим ВСЮ логистику srid (и прямую, и обратную),
        # если по srid есть хотя бы один явный маркер отмены/возврата.
        if srid:
            if srid in cancel_return_srids:
                logistics_cancel_return_sum += row_logistics_cost
            else:
                logistics_adjustments_sum += row_logistics_cost
        else:
            # Без srid не можем парно связать прямую/обратную, используем маркер строки.
            if is_cancel_marker:
                logistics_cancel_return_sum += row_logistics_cost
            else:
                logistics_adjustments_sum += row_logistics_cost

    buyouts_count = len(buyout_srids) + buyout_rows_without_srid
    cancel_return_count = len(cancel_return_srids) + cancel_return_rows_without_srid
    buyout_sale_prices = list(buyout_sale_price_by_srid.values()) + buyout_sale_price_without_srid
    sale_base_sum = sum(buyout_sale_prices)
    avg_retail_price_withdisc = (
        sale_base_sum / len(buyout_sale_prices)
        if buyout_sale_prices
        else 0.0
    )
    acquiring_percent_of_sale_base = (
        (acquiring_sum / sale_base_sum) * 100.0
        if sale_base_sum > 0
        else 0.0
    )

    # Штрафы WB приходят как raw_payload.penalty. Распределяем всю сумму штрафов
    # по артикулам пропорционально сумме продаж за период.
    penalty_total_period = 0.0
    jam_total_period = 0.0
    sale_base_sum_all_articles = 0.0
    seen_buyout_srids_all: set[str] = set()
    all_rows_qs = RealizationReportDetail.objects.filter(
        seller=seller,
        rr_dt__gte=date_from,
        rr_dt__lte=date_to,
    )
    cancel_return_srids_all: set[str] = set()
    for row in all_rows_qs.iterator(chunk_size=2000):
        payload = row.raw_payload or {}
        if _is_cancel_or_return_rr_row(row, payload):
            row_srid = (row.srid or "").strip()
            if row_srid:
                cancel_return_srids_all.add(row_srid)

    for row in all_rows_qs.iterator(chunk_size=2000):
        payload = row.raw_payload or {}
        penalty_total_period += max(_extract_penalty_from_raw(payload), 0.0)
        row_bonus_name = (row.bonus_type_name or "").strip().lower()
        if "джем" in row_bonus_name:
            jam_total_period += max(_to_float_or_default(row.deduction, 0.0), 0.0)

        row_for_pay = _extract_wb_for_pay_from_raw(payload)
        doc_type = (row.doc_type_name or "").strip().lower()
        bonus_type = (row.bonus_type_name or "").strip().lower()
        is_cancel_marker = _is_cancel_or_return_rr_row(row, payload)
        is_buyout = (not is_cancel_marker) and (("продаж" in doc_type) or ("при продаже" in bonus_type) or (row_for_pay > 0))
        if not is_buyout:
            continue

        sale_price_withdisc = _extract_retail_price_withdisc_from_raw(payload)
        if sale_price_withdisc <= 0:
            continue

        row_srid = (row.srid or "").strip()
        if row_srid:
            if row_srid in cancel_return_srids_all:
                continue
            if row_srid in seen_buyout_srids_all:
                continue
            seen_buyout_srids_all.add(row_srid)
        sale_base_sum_all_articles += sale_price_withdisc

    if penalty_total_period > 0 and sale_base_sum_all_articles > 0 and sale_base_sum > 0:
        penalty_share = sale_base_sum / sale_base_sum_all_articles
        penalty_sum = penalty_total_period * penalty_share
        penalty_is_approx = True
    if jam_total_period > 0 and sale_base_sum_all_articles > 0 and sale_base_sum > 0:
        jam_share = sale_base_sum / sale_base_sum_all_articles
        jam_sum = jam_total_period * jam_share
        jam_is_approx = True
    else:
        jam_sum = jam_direct_sum

    campaigns = list(WbAdvertCampaign.objects.filter(seller=seller))
    campaign_nm_map: dict[int, list[int]] = {}
    relevant_advert_ids: list[int] = []
    for c in campaigns:
        try:
            advert_id_int = int(c.advert_id)
        except (TypeError, ValueError):
            continue
        nm_ids = _extract_campaign_nm_ids_from_payload(c.raw_payload)
        if not nm_ids:
            continue
        if int(nm_id) not in nm_ids:
            continue
        campaign_nm_map[advert_id_int] = nm_ids
        relevant_advert_ids.append(advert_id_int)

    spend_by_advert = _build_campaign_spend_totals(
        seller=seller,
        advert_ids=relevant_advert_ids,
        date_from=date_from,
        date_to=date_to,
    )
    all_participant_nm_ids: set[int] = set()
    for values in campaign_nm_map.values():
        all_participant_nm_ids.update(int(v) for v in values if int(v) > 0)
    sales_base_by_nm_for_ads = _build_sales_base_by_nm(
        seller=seller,
        date_from=date_from,
        date_to=date_to,
        nm_ids=all_participant_nm_ids,
    )
    ad_spend_sum = 0.0
    for advert_id_int, participant_nm_ids in campaign_nm_map.items():
        campaign_total = float(spend_by_advert.get(advert_id_int, 0.0))
        allocated, is_approx = _allocate_campaign_spend_for_nm(
            target_nm_id=int(nm_id),
            campaign_nm_ids=participant_nm_ids,
            campaign_total_spend=campaign_total,
            sale_base_by_nm=sales_base_by_nm_for_ads,
        )
        ad_spend_sum += allocated
        if is_approx:
            ad_spend_is_approx = True

    # Хранение WB часто приходит агрегированными строками (nm_id=0) без привязки к артикулу.
    # В таком случае распределяем его приближенно по доле (общие FBO-остатки * объем артикула).
    storage_sum = 0.0
    storage_is_approx = False
    storage_total_nm0 = _to_float_or_default(
        RealizationReportDetail.objects.filter(
            seller=seller,
            rr_dt__gte=date_from,
            rr_dt__lte=date_to,
            nm_id=0,
        ).aggregate(total=Sum("storage_fee")).get("total"),
        0.0,
    )
    if storage_total_nm0 > 0:
        fbo_stock_by_nm = {
            int(r["nm_id"]): int(r.get("total_qty") or 0)
            for r in (
                WarehouseStockDetailed.objects
                .filter(seller=seller)
                .values("nm_id")
                .annotate(total_qty=Sum("quantity"))
            )
            if r.get("nm_id") is not None
        }
        all_nm_ids = list(fbo_stock_by_nm.keys())
        if all_nm_ids:
            volumes_by_nm = {
                int(r["nm_id"]): (
                    _to_float_or_default(r.get("volume_liters"), DEFAULT_LOGISTICS_VOLUME_LITERS)
                    if r.get("volume_liters") is not None
                    else DEFAULT_LOGISTICS_VOLUME_LITERS
                )
                for r in Product.objects.filter(seller=seller, nm_id__in=all_nm_ids).values("nm_id", "volume_liters")
            }
            total_weight = 0.0
            for article_nm_id, stock_qty in fbo_stock_by_nm.items():
                total_weight += stock_qty * _to_float_or_default(volumes_by_nm.get(article_nm_id), DEFAULT_LOGISTICS_VOLUME_LITERS)

            article_stock_qty = int(fbo_stock_by_nm.get(int(nm_id), 0))
            article_volume = _to_float_or_default(volumes_by_nm.get(int(nm_id)), DEFAULT_LOGISTICS_VOLUME_LITERS)
            article_weight = article_stock_qty * article_volume

            if total_weight > 0 and article_weight >= 0:
                storage_share = article_weight / total_weight
                storage_sum = storage_total_nm0 * storage_share
                storage_is_approx = True

    purchase_sum = max(purchase_price, 0.0) * buyouts_count
    defect_sum = purchase_sum * (max(settings_obj.defect_percent, 0.0) / 100.0)
    fulfillment_sum = max(settings_obj.fulfillment_cost_per_order, 0.0) * buyouts_count
    tax_sale_base_sum = sum(buyout_retail_amount_by_srid.values()) + sum(buyout_retail_amount_without_srid)
    if tax_sale_base_sum <= 0:
        # fallback для старых/неполных отчетов, где retailAmount не заполнен
        tax_sale_base_sum = max(discounted_price, 0.0) * buyouts_count
    tax_sum = (
        tax_sale_base_sum
        * (max(settings_obj.usn_percent, 0.0) / 100.0)
    )
    vat_sum = (
        tax_sale_base_sum
        * (max(settings_obj.vat_percent, 0.0) / 100.0)
    )
    logistics_sum = logistics_buyout_sum + logistics_cancel_return_sum + logistics_adjustments_sum
    taxes_vat_sum = tax_sum + vat_sum

    net_profit_sum = (
        wb_transfer_sum
        - logistics_sum
        - storage_sum
        - deduction_sum
        - jam_sum
        - penalty_sum
        - ad_spend_sum
        - acceptance_sum
        - purchase_sum
        - defect_sum
        - fulfillment_sum
        - taxes_vat_sum
    )
    sales_margin_percent = (
        (net_profit_sum / sale_base_sum) * 100.0
        if sale_base_sum > 0
        else 0.0
    )
    roi_base_sum = purchase_sum + fulfillment_sum + defect_sum
    roi_percent = (
        (net_profit_sum / roi_base_sum) * 100.0
        if roi_base_sum > 0
        else 0.0
    )
    avg_profit_per_buyout = (net_profit_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_logistics_buyout_per_buyout = (logistics_buyout_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_logistics_total_per_buyout = (logistics_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_logistics_cancel_return_per_return = (
        logistics_cancel_return_sum / cancel_return_count
        if cancel_return_count > 0
        else 0.0
    )
    avg_logistics_adjustments_per_buyout = (logistics_adjustments_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_logistics_negative_per_buyout = (logistics_negative_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_storage_per_buyout = (storage_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_deduction_per_buyout = (deduction_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_jam_per_buyout = (jam_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_penalty_per_buyout = (penalty_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_ad_spend_per_buyout = (ad_spend_sum / buyouts_count) if buyouts_count > 0 else 0.0
    avg_acceptance_per_buyout = (acceptance_sum / buyouts_count) if buyouts_count > 0 else 0.0
    # СПП в факте: приоритетно считаем по модели заказов
    # как разницу между ценой заказа и finished_price.
    spp_values_orders: list[float] = []
    if buyout_srids:
        order_rows_for_spp = (
            Order.objects
            .filter(
                seller=seller,
                srid__in=list(buyout_srids),
                is_cancel=False,
                is_return=False,
            )
            .exclude(order_price__isnull=True)
            .exclude(finished_price__isnull=True)
            .values("order_price", "finished_price")
        )
        for row in order_rows_for_spp:
            order_price = _to_float_or_default(row.get("order_price"), 0.0)
            finished_price = _to_float_or_default(row.get("finished_price"), 0.0)
            if order_price <= 0:
                continue
            spp_value = ((order_price - finished_price) / order_price) * 100.0
            spp_values_orders.append(spp_value)

    spp_values_reports = list(buyout_spp_by_srid.values()) + buyout_spp_without_srid
    spp_source = "orders" if spp_values_orders else "reports"
    spp_values = spp_values_orders if spp_values_orders else spp_values_reports
    spp_percent_fact = (sum(spp_values) / len(spp_values)) if spp_values else 0.0
    withheld_sum = deduction_sum + jam_sum + penalty_sum + acceptance_sum
    avg_withheld_per_buyout = (withheld_sum / buyouts_count) if buyouts_count > 0 else 0.0
    has_enough_data = bool(rr_rows) and buyouts_count > 0

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "has_enough_data": has_enough_data,
        "insufficient_data_message": insufficient_data_message,
        "buyouts_count": buyouts_count,
        "cancel_return_count": cancel_return_count,
        "wb_transfer_sum": round(wb_transfer_sum, 2),
        "avg_retail_price_withdisc": round(avg_retail_price_withdisc, 2),
        "sale_base_sum": round(sale_base_sum, 2),
        "acquiring_sum": round(acquiring_sum, 2),
        "acquiring_percent_of_sale_base": round(acquiring_percent_of_sale_base, 2),
        "logistics_buyout_sum": round(logistics_buyout_sum, 2),
        "logistics_cancel_return_sum": round(logistics_cancel_return_sum, 2),
        "logistics_adjustments_sum": round(logistics_adjustments_sum, 2),
        "logistics_negative_sum": round(logistics_negative_sum, 2),
        "avg_logistics_buyout_per_buyout": round(avg_logistics_buyout_per_buyout, 2),
        "avg_logistics_total_per_buyout": round(avg_logistics_total_per_buyout, 2),
        "avg_logistics_cancel_return_per_return": round(avg_logistics_cancel_return_per_return, 2),
        "avg_logistics_adjustments_per_buyout": round(avg_logistics_adjustments_per_buyout, 2),
        "avg_logistics_negative_per_buyout": round(avg_logistics_negative_per_buyout, 2),
        "storage_sum": round(storage_sum, 2),
        "storage_is_approx": storage_is_approx,
        "deduction_sum": round(deduction_sum, 2),
        "avg_deduction_per_buyout": round(avg_deduction_per_buyout, 2),
        "jam_sum": round(jam_sum, 2),
        "jam_is_approx": jam_is_approx,
        "avg_jam_per_buyout": round(avg_jam_per_buyout, 2),
        "penalty_sum": round(penalty_sum, 2),
        "penalty_is_approx": penalty_is_approx,
        "ad_spend_sum": round(ad_spend_sum, 2),
        "ad_spend_is_approx": ad_spend_is_approx,
        "avg_ad_spend_per_buyout": round(avg_ad_spend_per_buyout, 2),
        "acceptance_sum": round(acceptance_sum, 2),
        "spp_source": spp_source,
        "spp_samples_count": len(spp_values),
        "spp_percent_fact": round(spp_percent_fact, 2),
        "avg_storage_per_buyout": round(avg_storage_per_buyout, 2),
        "avg_penalty_per_buyout": round(avg_penalty_per_buyout, 2),
        "avg_acceptance_per_buyout": round(avg_acceptance_per_buyout, 2),
        "withheld_sum": round(withheld_sum, 2),
        "avg_withheld_per_buyout": round(avg_withheld_per_buyout, 2),
        "logistics_sum": round(logistics_sum, 2),
        "purchase_sum": round(purchase_sum, 2),
        "defect_sum": round(defect_sum, 2),
        "fulfillment_sum": round(fulfillment_sum, 2),
        "tax_sum": round(tax_sum, 2),
        "vat_sum": round(vat_sum, 2),
        "taxes_vat_sum": round(taxes_vat_sum, 2),
        "net_profit_sum": round(net_profit_sum, 2),
        "sales_margin_percent": round(sales_margin_percent, 2),
        "roi_percent": round(roi_percent, 2),
        "avg_profit_per_buyout": round(avg_profit_per_buyout, 2),
    }


def _get_seller_for_user(user):
    try:
        return user.seller_account
    except SellerAccount.DoesNotExist:
        return None


def _get_or_create_seller_for_user(user):
    seller = _get_seller_for_user(user)
    if seller:
        return seller
    display_name = user.get_full_name().strip() or user.username
    seller, _ = SellerAccount.objects.get_or_create(
        user=user,
        defaults={"name": display_name, "api_token": ""},
    )
    return seller


def _set_sync_task(task_id: str, payload: dict) -> None:
    user_id = payload.pop("user_id", None)
    seller_id = payload.pop("seller_id", None)
    defaults = {
        "status": payload.get("status") or SyncTask.STATUS_RUNNING,
        "progress": int(payload.get("progress") or 0),
        "step": payload.get("step") or "",
        "message": payload.get("message") or "",
        "result": payload.get("result") or {},
        "finished_at": payload.get("finished_at"),
    }
    if user_id is not None:
        defaults["user_id"] = user_id
    if seller_id is not None:
        defaults["seller_id"] = seller_id
    last_exc = None
    for attempt in range(1, 8):
        try:
            SyncTask.objects.update_or_create(task_id=task_id, defaults=defaults)
            return
        except Exception as exc:
            if not _is_db_locked_error(exc):
                raise
            last_exc = exc
            close_old_connections()
            time.sleep(min(2.0, 0.12 * attempt))
    if last_exc:
        raise last_exc


def _get_sync_stage_success_map(seller: SellerAccount | None) -> dict:
    if not seller:
        return {}
    meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
    stage_map = meta.get("stage_success_at")
    if isinstance(stage_map, dict):
        return dict(stage_map)
    return {}


def _get_stage_success_at(seller: SellerAccount | None, stage_key: str):
    stage_map = _get_sync_stage_success_map(seller)
    raw_value = stage_map.get(stage_key)
    if not raw_value:
        return None
    dt = parse_datetime(str(raw_value))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _mark_stage_success(seller: SellerAccount, stage_key: str, dt=None) -> None:
    if not seller:
        return
    when = dt or timezone.now()
    meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
    stage_map = meta.get("stage_success_at")
    if not isinstance(stage_map, dict):
        stage_map = {}
    stage_map[str(stage_key)] = when.isoformat()
    meta["stage_success_at"] = stage_map
    seller.sync_meta = meta
    _run_with_db_lock_retry(lambda: seller.save(update_fields=["sync_meta"]))


def _parse_hhmm(raw_value: str | None):
    value = (raw_value or "").strip()
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except (TypeError, ValueError):
        return None
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return dt_time(hour=hh, minute=mm)


def _get_auto_sync_config(seller: SellerAccount) -> dict:
    meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
    auto = meta.get("auto_sync")
    if not isinstance(auto, dict):
        auto = {}
    enabled = bool(auto.get("enabled", False))
    run_time = str(auto.get("time") or "").strip()
    last_run_date = str(auto.get("last_run_date") or "").strip()
    return {
        "enabled": enabled,
        "time": run_time,
        "last_run_date": last_run_date,
    }


def _save_auto_sync_config(seller: SellerAccount, *, enabled: bool, run_time: str, last_run_date: str | None = None) -> None:
    meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
    auto = meta.get("auto_sync")
    if not isinstance(auto, dict):
        auto = {}
    auto["enabled"] = bool(enabled)
    auto["time"] = run_time
    if last_run_date is not None:
        auto["last_run_date"] = str(last_run_date)
    meta["auto_sync"] = auto
    seller.sync_meta = meta
    _run_with_db_lock_retry(lambda: seller.save(update_fields=["sync_meta"]))


def _maybe_start_scheduled_sync_for_user(user, seller: SellerAccount | None) -> None:
    if not user or not getattr(user, "is_authenticated", False):
        return
    if not seller or not seller.has_api_token:
        return
    cfg = _get_auto_sync_config(seller)
    if not cfg.get("enabled"):
        return
    run_time_raw = str(cfg.get("time") or "").strip()
    run_time_obj = _parse_hhmm(run_time_raw)
    if run_time_obj is None:
        return
    now_local = timezone.localtime()
    today = now_local.date()
    if now_local.time() < run_time_obj:
        return
    if str(cfg.get("last_run_date") or "") == today.isoformat():
        return
    running_task = _get_running_sync_task_for_user(user)
    if running_task:
        return

    task_id = uuid.uuid4().hex
    _set_sync_task(
        task_id,
        {
            "task_id": task_id,
            "status": "running",
            "progress": 0,
            "step": "Инициализация",
            "message": "Авто-синхронизация запущена по расписанию...",
            "finished_at": None,
            "result": {},
            "user_id": user.id,
            "seller_id": seller.id,
        },
    )

    worker = threading.Thread(
        target=_run_sync_orders_task,
        args=(task_id, seller.id, user.id),
        daemon=True,
    )
    worker.start()
    _save_auto_sync_config(
        seller,
        enabled=True,
        run_time=run_time_raw,
        last_run_date=today.isoformat(),
    )


def _should_skip_stage_by_ttl(seller: SellerAccount, stage_key: str, ttl_hours: int = SYNC_STAGE_TTL_HOURS) -> bool:
    last_success_at = _get_stage_success_at(seller, stage_key)
    if not last_success_at:
        return False
    threshold = timezone.now() - timedelta(hours=int(ttl_hours))
    return last_success_at >= threshold


def _recommended_orders_days_back(seller: SellerAccount, fallback_days: int = 175, overlap_days: int = 3) -> int:
    max_last_change = Order.objects.filter(seller=seller).aggregate(max_dt=Max("last_change_date")).get("max_dt")
    if max_last_change:
        current_date = timezone.localdate()
        delta_days = (current_date - max_last_change.date()).days
        return max(overlap_days, delta_days + overlap_days)
    return int(fallback_days)


def _recommended_realization_date_from(seller: SellerAccount, fallback_days: int = 175, overlap_days: int = 14) -> date:
    max_rr_dt = RealizationReportDetail.objects.filter(seller=seller).aggregate(max_dt=Max("rr_dt")).get("max_dt")
    if max_rr_dt:
        return max_rr_dt - timedelta(days=int(overlap_days))
    return timezone.localdate() - timedelta(days=int(fallback_days))


def _recommended_ads_date_from(seller: SellerAccount, fallback_days: int = 30, overlap_days: int = 7) -> date:
    today = timezone.localdate()
    rolling_from = today - timedelta(days=int(fallback_days))
    agg = WbAdvertStatDaily.objects.filter(seller=seller).aggregate(
        min_dt=Min("stat_date"),
        max_dt=Max("stat_date"),
    )
    min_stat_date = agg.get("min_dt")
    max_stat_date = agg.get("max_dt")
    if not max_stat_date:
        return rolling_from
    # Если в БД еще нет полного окна за fallback_days, продолжаем backfill за весь период.
    if not min_stat_date or min_stat_date > rolling_from:
        return rolling_from
    return max(rolling_from, max_stat_date - timedelta(days=int(overlap_days)))


def _get_sync_task(task_id: str) -> SyncTask | None:
    return SyncTask.objects.filter(task_id=task_id).first()


def _expire_stale_running_sync_tasks_for_user(user) -> int:
    """
    Помечает "зависшие" running-задачи как error.
    Задача считается зависшей, если давно не обновлялась.
    """
    threshold = timezone.now() - timedelta(minutes=SYNC_TASK_STALE_MINUTES)
    stale_qs = SyncTask.objects.filter(
        user=user,
        status=SyncTask.STATUS_RUNNING,
        updated_at__lt=threshold,
    )
    updated = 0
    for task in stale_qs.iterator():
        task.status = SyncTask.STATUS_ERROR
        task.progress = max(task.progress or 0, 100)
        current_message = (task.message or "").strip()
        timeout_message = (
            f"{current_message} "
            f"(авто-остановка: задача не обновлялась более {SYNC_TASK_STALE_MINUTES} минут)"
        ).strip()
        task.message = timeout_message
        task.finished_at = timezone.now()
        task.save(update_fields=["status", "progress", "message", "finished_at", "updated_at"])
        updated += 1
    return updated


def _get_running_sync_task_for_user(user) -> SyncTask | None:
    _expire_stale_running_sync_tasks_for_user(user)
    return (
        SyncTask.objects
        .filter(user=user, status=SyncTask.STATUS_RUNNING)
        .order_by("-created_at")
        .first()
    )


def _normalize_name_for_match(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _resolve_name_case_insensitive(raw_value: str | None, options: list[str]) -> str | None:
    normalized = _normalize_name_for_match(raw_value)
    if not normalized:
        return None
    for option in options:
        if _normalize_name_for_match(option) == normalized:
            return option
    return (raw_value or "").strip() or None


def _get_last_sync_at_for_user(user, seller=None):
    last_sync_task = (
        SyncTask.objects
        .filter(user=user, status=SyncTask.STATUS_SUCCESS, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    if last_sync_task:
        return last_sync_task.finished_at

    if not seller:
        return None

    # Fallback: если SyncTask не заполнялся, берём самую свежую дату обновления данных продавца.
    candidates = []
    candidates.append(
        Order.objects.filter(seller=seller).aggregate(max_dt=Max("created_at")).get("max_dt")
    )
    candidates.append(
        WarehouseStockDetailed.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        WbWarehouseTariff.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        WbAcceptanceCoefficient.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        TransitDirectionTariff.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        RealizationReportDetail.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        SellerWarehouse.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        SellerFbsStock.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )

    dates = [dt for dt in candidates if dt is not None]
    return max(dates) if dates else None


def _log_app_error(
    *,
    source: str,
    message: str,
    user=None,
    seller=None,
    path: str = "",
    context: dict | None = None,
    traceback_text: str = "",
) -> None:
    try:
        AppErrorLog.objects.create(
            source=source,
            message=message,
            user=user,
            seller=seller,
            path=path or "",
            context_json=context or {},
            traceback_text=traceback_text or "",
        )
    except Exception:
        # Логирование в БД не должно ломать пользовательский поток.
        # Оставляем fallback в stderr для последующей диагностики.
        traceback.print_exc()


def _friendly_api_error_text(exc: Exception | str) -> str:
    raw = str(exc or "").strip()
    lowered = raw.lower()

    if "read-only token scope not allowed for this route" in lowered:
        return (
            "Ваш API токен подходит только для чтения. "
            "Для расширенных функций поменяйте токен на токен с правами записи в кабинете WB."
        )
    if "401" in lowered and ("unauthorized" in lowered or "не авториз" in lowered):
        return "Ошибка авторизации WB API. Проверьте токен: он может быть недействительным или без нужных прав."
    if "403" in lowered or "forbidden" in lowered or "доступ запрещ" in lowered:
        return "Доступ к этому методу WB API запрещён для текущего токена. Проверьте права доступа."
    if "429" in lowered or "too many requests" in lowered:
        return "Превышен лимит WB API. Подождите и повторите попытку."
    if "connection reset by peer" in lowered or "max retries exceeded" in lowered:
        return "Временная сетевая ошибка при обращении к WB API. Повторите попытку позже."
    if "name resolution" in lowered or "failed to resolve" in lowered:
        return "Не удалось подключиться к WB API (ошибка DNS/сети). Повторите попытку позже."
    return raw or "Неизвестная ошибка WB API."


def _ui_error_message(prefix: str, exc: Exception | str) -> str:
    return f"{prefix}: {_friendly_api_error_text(exc)}"


def _is_db_locked_error(exc: Exception) -> bool:
    """
    Универсальная проверка для SQLite lock ошибок:
    - django.db.OperationalError
    - sqlite3.OperationalError
    - обернутые исключения с той же строкой.
    """
    if isinstance(exc, (OperationalError, sqlite3.OperationalError)):
        text = str(exc).lower()
        return "database is locked" in text or "database table is locked" in text
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text


def _run_with_db_lock_retry(fn, *, attempts: int = 18):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not _is_db_locked_error(exc):
                raise
            last_exc = exc
            close_old_connections()
            time.sleep(min(3.0, 0.2 * attempt))
    if last_exc:
        raise last_exc
    return fn()


def _run_sync_orders_task(task_id: str, seller_id: int, user_id: int) -> None:
    close_old_connections()
    try:
        seller = SellerAccount.objects.filter(id=seller_id, user_id=user_id).first()
        if not seller:
            _set_sync_task(
                task_id,
                {
                    "task_id": task_id,
                    "status": "error",
                    "progress": 0,
                    "message": "SellerAccount не найден для текущего пользователя.",
                    "finished_at": timezone.now(),
                    "result": {},
                },
            )
            return

        result = {}
        skipped_steps = []
        today = timezone.localdate()
        ads_date_from = _recommended_ads_date_from(seller=seller, fallback_days=30, overlap_days=7)
        orders_days_back = _recommended_orders_days_back(seller=seller, fallback_days=INITIAL_SYNC_DAYS, overlap_days=3)
        realization_date_from = _recommended_realization_date_from(seller=seller, fallback_days=INITIAL_SYNC_DAYS, overlap_days=14)
        steps = [
            ("Карточки товаров", "products", sync_products_content, {"seller": seller}),
            ("Цены и скидки", "prices", sync_product_size_prices, {"seller": seller}),
            ("Комиссии категорий", "commissions", sync_category_commissions, {"seller": seller}),
            (
                "Рекламные кампании и статистика",
                "ads",
                sync_ad_campaigns_and_stats,
                {
                    "seller": seller,
                    "date_from": ads_date_from,
                    "date_to": today,
                },
            ),
            ("Тарифы коробов", "tariffs", sync_warehouse_tariffs, {"seller": seller}),
            ("Тарифы приёмки", "acceptance", sync_acceptance_coefficients, {"seller": seller}),
            ("Склады WB", "offices", sync_wb_offices, {"seller": seller}),
            ("Склады продавца", "seller_warehouses", sync_seller_warehouses, {"seller": seller}),
            ("Транзитные направления", "transit", sync_transit_direction_tariffs, {"seller": seller}),
            ("Заказы", "orders", sync_fbw_orders, {"seller": seller, "days_back": orders_days_back}),
            ("Продажи/возвраты WB", "sales", sync_sales_buyout_flags, {"seller": seller}),
            ("Остатки", "stocks", sync_supplier_stocks, {"seller": seller}),
            ("Остатки FBS", "fbs_stocks", sync_seller_fbs_stocks, {"seller": seller, "sync_card_sizes": False}),
        ]

        total_steps = len(steps) + 1  # + отчеты реализации
        for idx, (label, key, fn, kwargs) in enumerate(steps, start=1):
            _set_sync_task(
                task_id,
                {
                    "task_id": task_id,
                    "status": "running",
                    "progress": int(((idx - 1) / total_steps) * 100),
                    "step": label,
                    "message": f"Шаг {idx}/{total_steps}: {label}...",
                    "finished_at": None,
                    "result": result,
                },
            )
            if key in DAILY_SYNC_STAGES and _should_skip_stage_by_ttl(seller, key):
                result[key] = {"skipped": True, "reason": "already_synced_today"}
                skipped_steps.append(label)
                continue
            step_result = _run_with_db_lock_retry(lambda: fn(**kwargs))
            if isinstance(step_result, dict):
                result[key] = step_result
            else:
                result[key] = int(step_result or 0)
            _mark_stage_success(seller, key)

        ads_warning = None
        ads_result = result.get("ads")
        if isinstance(ads_result, dict) and ads_result.get("error"):
            ads_warning = str(ads_result.get("error"))

        realization_warning = None
        idx = total_steps
        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "running",
                "progress": int(((idx - 1) / total_steps) * 100),
                "step": "Отчёты реализации",
                "message": f"Шаг {idx}/{total_steps}: Отчёты реализации...",
                    "finished_at": None,
                    "result": result,
                },
            )
        try:
            realization_result = _run_with_db_lock_retry(
                lambda: sync_realization_report_detail(
                    seller=seller,
                    date_from=realization_date_from,
                    date_to=today,
                    period="weekly",
                    limit=10000,
                    respect_rate_limit=False,
                )
            )
            result["realization_rows"] = int(realization_result.get("upserted_rows") or 0)
            _mark_stage_success(seller, "realization")
        except Exception as exc:
            result["realization_rows"] = 0
            realization_warning = str(exc)

        message = (
            f"Синхронизация завершена: карточек {result.get('products', 0)}, "
            f"ценовых строк {result.get('prices', 0)}, "
            f"комиссий {result.get('commissions', 0)}, "
            f"рекламных кампаний {((result.get('ads') or {}).get('campaigns_synced', 0) if isinstance(result.get('ads'), dict) else 0)}, "
            f"строк рекламной статистики {((result.get('ads') or {}).get('stats_rows_upserted', 0) if isinstance(result.get('ads'), dict) else 0)}, "
            f"тарифов коробов {result.get('tariffs', 0)}, "
            f"тарифов приёмки {result.get('acceptance', 0)}, "
            f"транзитных направлений {result.get('transit', 0)}, "
            f"складов WB {result.get('offices', 0)}, складов продавца {result.get('seller_warehouses', 0)}, "
            f"заказов {result.get('orders', 0)}, "
            f"строк продаж/возвратов WB {((result.get('sales') or {}).get('rows', 0) if isinstance(result.get('sales'), dict) else result.get('sales', 0))}, "
            f"остатков {result.get('stocks', 0)}, "
            f"строк FBS-остатков {((result.get('fbs_stocks') or {}).get('stocks_rows', 0) if isinstance(result.get('fbs_stocks'), dict) else result.get('fbs_stocks', 0))}, "
            f"строк отчёта реализации {result.get('realization_rows', 0)}."
        )
        if realization_warning:
            message += f" Отчёты реализации частично пропущены: {realization_warning}"
        if ads_warning:
            message += f" Рекламная статистика частично пропущена: {ads_warning}"
        if skipped_steps:
            message += f" Пропущены по лимиту 1/день: {', '.join(skipped_steps)}."

        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "success",
                "progress": 100,
                "step": "Готово",
                "message": message,
                "finished_at": timezone.now(),
                "result": result,
            },
        )
    except Exception as exc:
        log_user = None
        log_seller = None
        if isinstance(user_id, int):
            from django.contrib.auth.models import User
            log_user = User.objects.filter(id=user_id).first()
        if isinstance(seller_id, int):
            log_seller = SellerAccount.objects.filter(id=seller_id).first()
        _log_app_error(
            source="sync.worker",
            message=f"Ошибка синхронизации: {exc}",
            user=log_user,
            seller=log_seller,
            context={"task_id": task_id},
            traceback_text=traceback.format_exc(),
        )
        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "error",
                "progress": 100,
                "step": "Ошибка",
                "message": _ui_error_message("Ошибка синхронизации", exc),
                "finished_at": timezone.now(),
                "result": {},
            },
        )
    finally:
        close_old_connections()


def home(request):
    if not request.user.is_authenticated:
        return render(request, "home_landing.html")

    seller = _get_or_create_seller_for_user(request.user)
    _maybe_start_scheduled_sync_for_user(request.user, seller)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    missing_api_token = not seller or not seller.has_api_token
    running_sync_task = _get_running_sync_task_for_user(request.user)
    running_sync_task_payload = None
    if running_sync_task:
        running_sync_task_payload = {
            "task_id": running_sync_task.task_id,
            "status": running_sync_task.status,
            "progress": running_sync_task.progress,
            "step": running_sync_task.step,
            "message": running_sync_task.message,
        }

    today = timezone.localdate()
    date_from_30d = today - timedelta(days=29)

    orders_30_qs = Order.objects.filter(
        seller=seller,
        order_date__date__gte=date_from_30d,
        order_date__date__lte=today,
    )
    orders_30_stats = orders_30_qs.aggregate(
        total_orders=Count("id"),
        buyouts=Count("id", filter=Q(is_buyout=True)),
        revenue=Sum("finished_price", filter=Q(is_buyout=True, finished_price__isnull=False)),
        local_orders=Count("id", filter=Q(is_local=True)),
        local_buyouts=Count("id", filter=Q(is_buyout=True, is_local=True)),
    )

    total_orders_30d = int(orders_30_stats.get("total_orders") or 0)
    buyouts_30d = int(orders_30_stats.get("buyouts") or 0)
    revenue_30d = float(orders_30_stats.get("revenue") or 0.0)
    local_orders_30d = int(orders_30_stats.get("local_orders") or 0)
    buyout_rate_30d = round((buyouts_30d / total_orders_30d) * 100.0, 2) if total_orders_30d > 0 else 0.0
    avg_check_30d = round(revenue_30d / buyouts_30d, 2) if buyouts_30d > 0 else 0.0
    local_share_orders_30d = round((local_orders_30d / total_orders_30d) * 100.0, 2) if total_orders_30d > 0 else 0.0

    products_count = Product.objects.filter(seller=seller).count()
    fbo_stock_total = int(
        WarehouseStockDetailed.objects.filter(seller=seller).aggregate(total=Sum("quantity")).get("total") or 0
    )
    fbs_stock_total = int(
        SellerFbsStock.objects.filter(seller=seller).aggregate(total=Sum("amount")).get("total") or 0
    )

    ad_spend_30d = float(
        WbAdvertStatDaily.objects
        .filter(seller=seller, stat_date__gte=date_from_30d, stat_date__lte=today)
        .aggregate(total=Sum("spend"))
        .get("total")
        or 0.0
    )
    active_ads_count = WbAdvertCampaign.objects.filter(seller=seller, status=9).count()

    daily_rows = list(
        orders_30_qs
        .annotate(day=TruncDate("order_date"))
        .values("day")
        .annotate(
            total=Count("id"),
            buyouts=Count("id", filter=Q(is_buyout=True)),
            revenue=Sum("finished_price", filter=Q(is_buyout=True, finished_price__isnull=False)),
        )
        .order_by("day")
    )
    daily_map = {
        row["day"]: {
            "total": int(row.get("total") or 0),
            "buyouts": int(row.get("buyouts") or 0),
            "revenue": round(float(row.get("revenue") or 0.0), 2),
        }
        for row in daily_rows
        if row.get("day")
    }

    daily_points = []
    for i in range(30):
        day = date_from_30d + timedelta(days=i)
        data = daily_map.get(day, {"total": 0, "buyouts": 0, "revenue": 0.0})
        daily_points.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "orders": data["total"],
                "buyouts": data["buyouts"],
                "revenue": data["revenue"],
            }
        )

    return render(
        request,
        "dashboard_main.html",
        {
            "seller": seller,
            "missing_api_token": missing_api_token,
            "last_sync_at": last_sync_at,
            "products_count": products_count,
            "total_orders_30d": total_orders_30d,
            "buyouts_30d": buyouts_30d,
            "buyout_rate_30d": buyout_rate_30d,
            "revenue_30d": round(revenue_30d, 2),
            "avg_check_30d": avg_check_30d,
            "local_share_orders_30d": local_share_orders_30d,
            "fbo_stock_total": fbo_stock_total,
            "fbs_stock_total": fbs_stock_total,
            "ad_spend_30d": round(ad_spend_30d, 2),
            "active_ads_count": active_ads_count,
            "daily_points_json": daily_points,
            "running_sync_task": running_sync_task_payload,
        },
    )


@login_required
@require_GET
def dashboard_trend_api(request):
    seller = _get_or_create_seller_for_user(request.user)
    period = (request.GET.get("period") or "14d").strip().lower()
    metric = (request.GET.get("metric") or "orders").strip().lower()
    if period not in {"today", "7d", "14d", "28d"}:
        period = "14d"
    if metric not in {"orders", "buyouts"}:
        metric = "orders"

    now_local = timezone.localtime()
    today = now_local.date()
    tz = timezone.get_current_timezone()

    if period == "today":
        current_start_dt = timezone.make_aware(datetime.combine(today, dt_time.min), timezone=tz)
        current_end_dt = current_start_dt + timedelta(days=1)
        previous_start_dt = current_start_dt - timedelta(days=1)
        previous_end_dt = current_start_dt
        labels = [f"{hour:02d}:00" for hour in range(24)]

        if metric == "buyouts":
            current_qs = (
                Order.objects
                .filter(seller=seller, is_buyout=True, buyout_date__gte=current_start_dt, buyout_date__lt=current_end_dt)
                .annotate(bucket=TruncHour("buyout_date", tzinfo=tz))
                .values("bucket")
            )
            previous_qs = (
                Order.objects
                .filter(seller=seller, is_buyout=True, buyout_date__gte=previous_start_dt, buyout_date__lt=previous_end_dt)
                .annotate(bucket=TruncHour("buyout_date", tzinfo=tz))
                .values("bucket")
            )
        else:
            current_qs = (
                Order.objects
                .filter(seller=seller, order_date__gte=current_start_dt, order_date__lt=current_end_dt)
                .annotate(bucket=TruncHour("order_date", tzinfo=tz))
                .values("bucket")
            )
            previous_qs = (
                Order.objects
                .filter(seller=seller, order_date__gte=previous_start_dt, order_date__lt=previous_end_dt)
                .annotate(bucket=TruncHour("order_date", tzinfo=tz))
                .values("bucket")
            )

        current_rows = current_qs.annotate(cnt=Count("id"))
        previous_rows = previous_qs.annotate(cnt=Count("id"))

        current_map = {int(timezone.localtime(r["bucket"]).hour): int(r.get("cnt") or 0) for r in current_rows if r.get("bucket")}
        previous_map = {int(timezone.localtime(r["bucket"]).hour): int(r.get("cnt") or 0) for r in previous_rows if r.get("bucket")}

        current_values = [int(current_map.get(hour, 0)) for hour in range(24)]
        previous_values = [int(previous_map.get(hour, 0)) for hour in range(24)]
    else:
        window_days = {"7d": 7, "14d": 14, "28d": 28}[period]
        current_start_date = today - timedelta(days=window_days - 1)
        current_end_date = today
        previous_end_date = current_start_date - timedelta(days=1)
        previous_start_date = previous_end_date - timedelta(days=window_days - 1)

        labels = []
        day = current_start_date
        while day <= current_end_date:
            labels.append(day.strftime("%d.%m"))
            day += timedelta(days=1)

        if metric == "buyouts":
            current_qs = (
                Order.objects
                .filter(
                    seller=seller,
                    is_buyout=True,
                    buyout_date__date__gte=current_start_date,
                    buyout_date__date__lte=current_end_date,
                )
                .annotate(bucket=TruncDate("buyout_date"))
                .values("bucket")
            )
            previous_qs = (
                Order.objects
                .filter(
                    seller=seller,
                    is_buyout=True,
                    buyout_date__date__gte=previous_start_date,
                    buyout_date__date__lte=previous_end_date,
                )
                .annotate(bucket=TruncDate("buyout_date"))
                .values("bucket")
            )
        else:
            current_qs = (
                Order.objects
                .filter(
                    seller=seller,
                    order_date__date__gte=current_start_date,
                    order_date__date__lte=current_end_date,
                )
                .annotate(bucket=TruncDate("order_date"))
                .values("bucket")
            )
            previous_qs = (
                Order.objects
                .filter(
                    seller=seller,
                    order_date__date__gte=previous_start_date,
                    order_date__date__lte=previous_end_date,
                )
                .annotate(bucket=TruncDate("order_date"))
                .values("bucket")
            )

        current_rows = current_qs.annotate(cnt=Count("id"))
        previous_rows = previous_qs.annotate(cnt=Count("id"))

        current_map = {r["bucket"]: int(r.get("cnt") or 0) for r in current_rows if r.get("bucket")}
        previous_map = {r["bucket"]: int(r.get("cnt") or 0) for r in previous_rows if r.get("bucket")}

        current_values = []
        previous_values = []
        for i in range(window_days):
            current_day = current_start_date + timedelta(days=i)
            previous_day = previous_start_date + timedelta(days=i)
            current_values.append(int(current_map.get(current_day, 0)))
            previous_values.append(int(previous_map.get(previous_day, 0)))

    current_total = int(sum(current_values))
    previous_total = int(sum(previous_values))
    delta_abs = int(current_total - previous_total)
    delta_percent = round((delta_abs / previous_total) * 100.0, 2) if previous_total > 0 else None

    return JsonResponse(
        {
            "labels": labels,
            "current_values": current_values,
            "previous_values": previous_values,
            "current_total": current_total,
            "previous_total": previous_total,
            "delta_abs": delta_abs,
            "delta_percent": delta_percent,
            "updated_at": now_local.isoformat(),
        },
        status=200,
    )


def analytics_logistics(request):
    if not request.user.is_authenticated:
        return render(request, "home_landing.html")

    seller = _get_or_create_seller_for_user(request.user)
    _maybe_start_scheduled_sync_for_user(request.user, seller)

    missing_api_token = not seller or not seller.has_api_token

    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    running_sync_task = _get_running_sync_task_for_user(request.user)
    running_sync_task_payload = None
    if running_sync_task:
        running_sync_task_payload = {
            "task_id": running_sync_task.task_id,
            "status": running_sync_task.status,
            "progress": running_sync_task.progress,
            "step": running_sync_task.step,
            "message": running_sync_task.message,
        }
    return render(
        request,
        "home.html",
        {
            "seller": seller,
            "missing_api_token": missing_api_token,
            "last_sync_at": last_sync_at,
            "running_sync_task": running_sync_task_payload,
        },
    )


@login_required
@require_GET
def analytics_logistics_data_api(request):
    seller = _get_or_create_seller_for_user(request.user)
    if not seller or not seller.has_api_token:
        return JsonResponse(
            {
                "ok": False,
                "error": "api_token_missing",
                "local_orders_percent": None,
                "local_orders_trend": {"points": []},
                "fact_localization_index_trend": {"points": []},
                "theoretical_localization_index_trend": {"points": []},
                "theoretical_irp_trend": {"points": []},
                "top_non_local_districts": {"points": []},
            },
            status=200,
        )

    payload = {
        "ok": True,
        "local_orders_percent": get_local_orders_percent_last_full_week(seller),
        "local_orders_trend": get_local_orders_percent_trend_last_full_weeks(seller, weeks=25),
        "fact_localization_index_trend": get_fact_localization_index_trend_last_full_weeks(seller, weeks=25),
        "theoretical_localization_index_trend": get_theoretical_localization_index_trend_last_full_weeks(seller, weeks=25),
        "theoretical_irp_trend": get_theoretical_irp_trend_last_full_weeks(seller, weeks=25),
        "top_non_local_districts": get_top_non_local_districts_last_full_weeks(seller, weeks=13, limit=5),
    }
    return JsonResponse(payload, status=200)


@login_required
@require_POST
def sync_orders_start_api(request):
    try:
        seller = _get_or_create_seller_for_user(request.user)
        if not seller.has_api_token:
            return JsonResponse(
                {"error": "Сначала добавьте API-ключ в настройках аккаунта."},
                status=400,
            )

        running_task = _get_running_sync_task_for_user(request.user)
        if running_task:
            return JsonResponse(
                {
                    "error": "Синхронизация уже выполняется. Дождитесь завершения текущей задачи.",
                    "task_id": running_task.task_id,
                    "status": running_task.status,
                    "progress": running_task.progress,
                    "step": running_task.step,
                    "message": running_task.message,
                },
                status=409,
            )

        task_id = uuid.uuid4().hex
        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "running",
                "progress": 0,
                "step": "Инициализация",
                "message": "Задача синхронизации запущена...",
                "finished_at": None,
                "result": {},
                "user_id": request.user.id,
                "seller_id": seller.id,
            },
        )

        worker = threading.Thread(
            target=_run_sync_orders_task,
            args=(task_id, seller.id, request.user.id),
            daemon=True,
        )
        worker.start()
        return JsonResponse({"task_id": task_id, "status": "running"}, status=202)
    except Exception as exc:
        _log_app_error(
            source="sync.start_api",
            message=f"Не удалось запустить синхронизацию: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Не удалось запустить синхронизацию. Попробуйте позже."}, status=500)


@login_required
@require_GET
def sync_orders_status_api(request):
    try:
        task_id = (request.GET.get("task_id") or "").strip()
        if not task_id:
            return JsonResponse({"error": "task_id is required"}, status=400)

        task = _get_sync_task(task_id)
        if not task:
            return JsonResponse({"error": "task not found"}, status=404)
        if task.user_id != request.user.id:
            return JsonResponse({"error": "forbidden"}, status=403)

        if task.status == SyncTask.STATUS_RUNNING:
            _expire_stale_running_sync_tasks_for_user(request.user)
            task.refresh_from_db()


        payload = {
            "task_id": task.task_id,
            "status": task.status,
            "progress": task.progress,
            "step": task.step,
            "message": task.message,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "result": task.result or {},
        }
        return JsonResponse(payload, status=200)
    except Exception as exc:
        _log_app_error(
            source="sync.status_api",
            message=f"Не удалось получить статус синхронизации: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            context={"task_id": request.GET.get("task_id")},
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Не удалось получить статус синхронизации. Попробуйте позже."}, status=500)


@login_required
@require_GET
def sync_orders_current_api(request):
    try:
        running_task = _get_running_sync_task_for_user(request.user)
        if not running_task:
            return JsonResponse({"has_running": False}, status=200)
        return JsonResponse(
            {
                "has_running": True,
                "task_id": running_task.task_id,
                "status": running_task.status,
                "progress": running_task.progress,
                "step": running_task.step,
                "message": running_task.message,
            },
            status=200,
        )
    except Exception as exc:
        _log_app_error(
            source="sync.current_api",
            message=f"Не удалось получить текущую синхронизацию: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Не удалось получить текущую синхронизацию. Попробуйте позже."}, status=500)


@login_required
def replenishment_report(request):
    seller = _get_seller_for_user(request.user)
    data = calculate_replenishment(seller) if seller else []
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    return render(
        request,
        "replenishment/report.html",
        {"rows": data, "seller": seller, "last_sync_at": last_sync_at}
    )


@login_required
def account_settings(request):
    seller = _get_or_create_seller_for_user(request.user)
    auto_sync_cfg = _get_auto_sync_config(seller)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "save_auto_sync":
            enabled = (request.POST.get("auto_sync_enabled") or "").strip() in {"1", "true", "on", "yes"}
            run_time_raw = (request.POST.get("auto_sync_time") or "").strip()
            run_time_obj = _parse_hhmm(run_time_raw)
            if enabled and run_time_obj is None:
                messages.error(request, "Укажите корректное время автосинхронизации в формате ЧЧ:ММ.")
                return redirect(reverse("account_settings"))
            normalized_time = run_time_obj.strftime("%H:%M") if run_time_obj else "09:00"
            _save_auto_sync_config(
                seller,
                enabled=enabled,
                run_time=normalized_time,
                last_run_date=auto_sync_cfg.get("last_run_date") or "",
            )
            messages.success(
                request,
                "Настройки авто-синхронизации сохранены."
                if enabled
                else "Авто-синхронизация отключена.",
            )
            return redirect(reverse("account_settings"))

        if action == "purge_seller_data":
            confirmed = (request.POST.get("confirm_purge_seller_data") or "").strip() == "1"
            if not confirmed:
                messages.error(request, "Удаление данных отменено: не подтверждено.")
                return redirect(reverse("account_settings"))

            deleted_summary = {
                "products": Product.objects.filter(seller=seller).delete()[0],
                "product_prices": ProductSizePrice.objects.filter(seller=seller).delete()[0],
                "commissions": WbCategoryCommission.objects.filter(seller=seller).delete()[0],
                "orders": Order.objects.filter(seller=seller).delete()[0],
                "stocks": WarehouseStockDetailed.objects.filter(seller=seller).delete()[0],
                "tariffs": WbWarehouseTariff.objects.filter(seller=seller).delete()[0],
                "acceptance": WbAcceptanceCoefficient.objects.filter(seller=seller).delete()[0],
                "transit": TransitDirectionTariff.objects.filter(seller=seller).delete()[0],
                "realization": RealizationReportDetail.objects.filter(seller=seller).delete()[0],
                "sync_tasks": SyncTask.objects.filter(seller=seller).delete()[0],
                "feedback": TesterFeedback.objects.filter(seller=seller).delete()[0],
                "errors": AppErrorLog.objects.filter(seller=seller).delete()[0],
            }
            total_deleted = sum(deleted_summary.values())
            messages.success(
                request,
                (
                    "Данные seller очищены. "
                    f"Удалено записей: {total_deleted} "
                    f"(заказы: {deleted_summary['orders']}, товары: {deleted_summary['products']})."
                ),
            )
            return redirect(reverse("account_settings"))

        if action == "delete_account":
            confirmed = (request.POST.get("confirm_delete_account") or "").strip() == "1"
            if not confirmed:
                messages.error(request, "Удаление аккаунта отменено: не подтверждено.")
                return redirect(reverse("account_settings"))

            user = request.user
            logout(request)
            user.delete()
            return redirect(reverse("home"))

        token_input = (request.POST.get("api_token") or "").strip()
        if token_input:
            seller.set_api_token(token_input)
            seller.save(update_fields=["api_token"])
            return redirect(f"{reverse('account_settings')}?saved=1")
        messages.info(request, "API-ключ не изменён.")
        return redirect(reverse("account_settings"))

    return render(
        request,
        "account/settings.html",
        {
            "seller": seller,
            "saved": request.GET.get("saved") == "1",
            "auto_sync_enabled": bool(auto_sync_cfg.get("enabled", False)),
            "auto_sync_time": str(auto_sync_cfg.get("time") or "09:00"),
            "auto_sync_last_run_date": str(auto_sync_cfg.get("last_run_date") or ""),
        },
    )


@login_required
def supply_recommendations_report(request):
    seller = _get_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    default_date_to = current_month_start - timedelta(days=1)
    default_date_from = default_date_to.replace(day=1)

    transit_warehouses = list_available_transit_warehouses(seller=seller)
    main_warehouses = list_regular_warehouses(seller=seller)
    default_transit_warehouse = (
        "Обухово" if "Обухово" in transit_warehouses else (transit_warehouses[0] if transit_warehouses else "")
    )
    default_main_warehouse = "Электросталь" if "Электросталь" in main_warehouses else (main_warehouses[0] if main_warehouses else "")
    return render(
        request,
        "recommendations/report.html",
        {
            "seller": seller,
            "default_date_from": default_date_from.isoformat(),
            "default_date_to": default_date_to.isoformat(),
            "transit_warehouses": transit_warehouses,
            "default_transit_warehouse": default_transit_warehouse,
            "main_warehouses": main_warehouses,
            "default_main_warehouse": default_main_warehouse,
            "last_sync_at": last_sync_at,
        },
    )


@login_required
@require_GET
def dashboard_supply_recommendations_api(request):
    date_from_raw = request.GET.get("date_from")
    date_to_raw = request.GET.get("date_to")
    transit_warehouse = (request.GET.get("transit_warehouse") or "").strip()
    main_warehouse = (request.GET.get("main_warehouse") or "").strip()
    include_food = (request.GET.get("include_food") or "").strip().lower() in {"1", "true", "yes", "on"}

    if not date_from_raw or not date_to_raw:
        return JsonResponse(
            {"error": "date_from and date_to are required query params in YYYY-MM-DD format"},
            status=400,
        )

    try:
        date_from = date.fromisoformat(date_from_raw)
        date_to = date.fromisoformat(date_to_raw)
    except ValueError:
        return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

    if date_from > date_to:
        return JsonResponse({"error": "date_from must be <= date_to"}, status=400)

    try:
        seller = _get_seller_for_user(request.user)
        transit_warehouse = _resolve_name_case_insensitive(
            transit_warehouse,
            list_available_transit_warehouses(seller=seller),
        ) or ""
        main_warehouse = _resolve_name_case_insensitive(
            main_warehouse,
            list_regular_warehouses(seller=seller),
        ) or ""

        payload = get_dashboard_supply_recommendations(
            date_from=date_from,
            date_to=date_to,
            seller=seller,
            transit_warehouse=transit_warehouse or None,
            main_warehouse=main_warehouse or None,
            include_food=include_food,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        _log_app_error(
            source="recommendations.api",
            message=f"Internal error while building recommendations: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            context={
                "date_from": date_from_raw,
                "date_to": date_to_raw,
                "transit_warehouse": transit_warehouse,
                "main_warehouse": main_warehouse,
                "include_food": include_food,
            },
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Internal error while building recommendations"}, status=500)

    return JsonResponse(payload, status=200)


@login_required
def acceptance_coefficients_report(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    if request.method == "POST" and request.POST.get("action") == "sync_acceptance":
        if not seller.has_api_token:
            messages.error(request, "Сначала добавьте API-ключ в настройках аккаунта.")
            return redirect("acceptance_coefficients_report")
        try:
            synced = sync_acceptance_coefficients(seller)
            messages.success(request, f"Синхронизация коэффициентов приёмки завершена: {synced} строк.")
        except Exception as exc:
            _log_app_error(
                source="acceptance.sync",
                message=f"Ошибка синхронизации коэффициентов приёмки: {exc}",
                user=request.user,
                seller=seller,
                path=request.path,
                traceback_text=traceback.format_exc(),
            )
            messages.error(request, _ui_error_message("Ошибка синхронизации коэффициентов приёмки", exc))
        return redirect("acceptance_coefficients_report")

    date_from_raw = request.GET.get("date_from")
    date_to_raw = request.GET.get("date_to")
    warehouse_query = (request.GET.get("warehouse") or "").strip()
    box_type = (request.GET.get("box_type") or "2").strip()
    only_available = request.GET.get("only_available", "1") == "1"
    hide_sc = request.GET.get("hide_sc") == "1"
    hide_food = request.GET.get("hide_food", "1") == "1"

    today = timezone.localdate()
    default_date_from = today
    default_date_to = today + timedelta(days=13)

    try:
        date_from = date.fromisoformat(date_from_raw) if date_from_raw else default_date_from
    except ValueError:
        date_from = default_date_from
    try:
        date_to = date.fromisoformat(date_to_raw) if date_to_raw else default_date_to
    except ValueError:
        date_to = default_date_to

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    qs = WbAcceptanceCoefficient.objects.filter(
        seller=seller,
        coeff_date__gte=date_from,
        coeff_date__lte=date_to,
    )
    if box_type != "all":
        try:
            qs = qs.filter(box_type_id=int(box_type))
        except ValueError:
            box_type = "2"
            qs = qs.filter(box_type_id=2)
    normalized_warehouse_query = _normalize_name_for_match(warehouse_query)
    if only_available:
        # "Доступные к отгрузке" = разрешена отгрузка и нет запрета по коэффициенту.
        qs = qs.filter(allow_unload=True).exclude(coefficient__lt=0)

    raw_rows = list(qs.order_by("warehouse_name", "coeff_date"))

    def _is_sc_name(name: str) -> bool:
        normalized = _normalize_name_for_match(name)
        return normalized.startswith("сц ")

    def _is_food_name(name: str) -> bool:
        normalized = _normalize_name_for_match(name)
        return "питание" in normalized

    filtered_rows = []
    for row in raw_rows:
        warehouse_name = (row.warehouse_name or "").strip()
        if not warehouse_name:
            continue
        if hide_sc and (_is_sc_name(warehouse_name) or bool(row.is_sorting_center)):
            continue
        if hide_food and _is_food_name(warehouse_name):
            continue
        if normalized_warehouse_query and normalized_warehouse_query not in _normalize_name_for_match(warehouse_name):
            continue
        filtered_rows.append(row)

    warehouse_options_qs = (
        WbAcceptanceCoefficient.objects
        .filter(seller=seller)
        .exclude(warehouse_name__isnull=True)
        .exclude(warehouse_name__exact="")
    )
    if box_type != "all":
        try:
            warehouse_options_qs = warehouse_options_qs.filter(box_type_id=int(box_type))
        except ValueError:
            pass
    warehouse_options = sorted(
        set(warehouse_options_qs.values_list("warehouse_name", flat=True))
    )

    date_columns = []
    current = date_from
    while current <= date_to:
        date_columns.append(current)
        current += timedelta(days=1)

    def _fmt_value(value):
        if value is None:
            return "-"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _fmt_pair(first, second):
        if first is None and second is None:
            return "-"
        return f"{_fmt_value(first)} / {_fmt_value(second)}"

    def _fmt_coef(value):
        if value is None:
            return ""
        return f"{value:.0f}%"

    if box_type == "5":
        delivery_title = "Логистика, ₽ за паллету"
        storage_title = "Хранение, ₽ за паллету"
    else:
        delivery_title = "Логистика, ₽ (1-й / доп. литр)"
        storage_title = "Хранение, ₽ (1-й / доп. литр)"

    def _format_delivery(row):
        if box_type == "5":
            return _fmt_value(row.delivery_base_liter)
        return _fmt_pair(row.delivery_base_liter, row.delivery_additional_liter)

    def _format_storage(row):
        if box_type == "5":
            return _fmt_value(row.storage_base_liter)
        return _fmt_pair(row.storage_base_liter, row.storage_additional_liter)

    matrix = {}
    warehouse_names = set()
    for row in filtered_rows:
        warehouse_name = (row.warehouse_name or "").strip()
        if not warehouse_name:
            continue
        warehouse_names.add(warehouse_name)

        if not row.allow_unload or (row.coefficient is not None and row.coefficient < 0):
            acceptance_label = "Недоступно"
            acceptance_class = "pill-bad"
        elif row.coefficient == 0:
            acceptance_label = "Бесплатно"
            acceptance_class = "pill-ok"
        elif row.coefficient is None:
            acceptance_label = "-"
            acceptance_class = "pill-neutral"
        else:
            acceptance_label = f"x{_fmt_value(row.coefficient)}"
            acceptance_class = "pill-warn"

        matrix[(warehouse_name, row.coeff_date)] = {
            "acceptance_label": acceptance_label,
            "acceptance_class": acceptance_class,
            "logistics_text": _format_delivery(row),
            "logistics_coef_text": _fmt_coef(row.delivery_coef),
            "storage_text": _format_storage(row),
            "storage_coef_text": _fmt_coef(row.storage_coef),
        }

    warehouse_rows = []
    for warehouse_name in sorted(warehouse_names):
        cells = [matrix.get((warehouse_name, dt)) for dt in date_columns]
        warehouse_rows.append({"warehouse_name": warehouse_name, "cells": cells})

    return render(
        request,
        "tariffs/acceptance_coefficients.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "date_columns": date_columns,
            "warehouse_rows": warehouse_rows,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "warehouse_query": warehouse_query,
            "warehouse_options": warehouse_options,
            "box_type": box_type,
            "delivery_title": delivery_title,
            "storage_title": storage_title,
            "only_available": only_available,
            "hide_sc": hide_sc,
            "hide_food": hide_food,
        },
    )


@login_required
def seller_warehouses_report(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    if request.method == "POST" and request.POST.get("action") == "sync_seller_warehouses":
        if not seller.has_api_token:
            messages.error(request, "Сначала добавьте API-ключ в настройках аккаунта.")
            return redirect("seller_warehouses_report")
        try:
            synced = sync_seller_warehouses(seller)
            messages.success(request, f"Синхронизация складов продавца завершена: {synced} строк.")
        except Exception as exc:
            _log_app_error(
                source="seller_warehouses.sync",
                message=f"Ошибка синхронизации складов продавца: {exc}",
                user=request.user,
                seller=seller,
                path=request.path,
                traceback_text=traceback.format_exc(),
            )
            messages.error(request, _ui_error_message("Ошибка синхронизации складов продавца", exc))
        return redirect("seller_warehouses_report")

    rows = SellerWarehouse.objects.filter(seller=seller).order_by("name", "seller_warehouse_id")
    return render(
        request,
        "warehouses/seller_warehouses.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "rows": rows,
        },
    )


@login_required
def fbs_stocks_report(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    warehouse_filter = (request.GET.get("warehouse") or "").strip()
    query = (request.GET.get("q") or "").strip()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if not seller.has_api_token:
            messages.error(request, "Сначала добавьте API-ключ в настройках аккаунта.")
            return redirect("fbs_stocks_report")

        if action == "sync_fbs_stocks":
            try:
                result = sync_seller_fbs_stocks(seller=seller)
                messages.success(
                    request,
                    (
                        "Синхронизация FBS-остатков завершена: "
                        f"размеров {result.get('sizes_synced', 0)}, "
                        f"складов FBS {result.get('warehouses', 0)}, "
                        f"строк остатков {result.get('stocks_rows', 0)}."
                    ),
                )
            except Exception as exc:
                _log_app_error(
                    source="fbs_stocks.sync",
                    message=f"Ошибка синхронизации FBS-остатков: {exc}",
                    user=request.user,
                    seller=seller,
                    path=request.path,
                    traceback_text=traceback.format_exc(),
                )
                messages.error(request, _ui_error_message("Ошибка синхронизации FBS-остатков", exc))
            return redirect("fbs_stocks_report")

        if action == "update_fbs_stocks":
            try:
                raw_changes = (request.POST.get("changes_json") or "").strip()
                changes = json.loads(raw_changes) if raw_changes else []
                if not isinstance(changes, list):
                    raise ValueError("Некорректный формат изменений.")
                result = apply_fbs_stock_updates(seller=seller, changes=changes)
                messages.success(
                    request,
                    (
                        "Изменения отправлены в WB: "
                        f"обновлено позиций {result.get('updated_rows', 0)}, "
                        f"затронуто складов {result.get('warehouses_touched', 0)}."
                    ),
                )
            except Exception as exc:
                _log_app_error(
                    source="fbs_stocks.update",
                    message=f"Ошибка обновления FBS-остатков: {exc}",
                    user=request.user,
                    seller=seller,
                    path=request.path,
                    traceback_text=traceback.format_exc(),
                )
                messages.error(request, _ui_error_message("Ошибка обновления FBS-остатков", exc))
            return redirect("fbs_stocks_report")

    size_map = {
        row.chrt_id: row
        for row in ProductCardSize.objects.filter(seller=seller)
    }
    warehouses = list(
        SellerWarehouse.objects.filter(seller=seller, delivery_type=1)
        .order_by("name")
        .values_list("name", flat=True)
    )

    rows_qs = SellerFbsStock.objects.filter(seller=seller)
    if warehouse_filter:
        rows_qs = rows_qs.filter(warehouse_name=warehouse_filter)
    rows_qs = rows_qs.select_related("seller_warehouse").order_by("-amount", "warehouse_name", "chrt_id")

    rows = []
    total_amount = 0
    for stock in rows_qs:
        size = size_map.get(stock.chrt_id)
        row = {
            "seller_warehouse_id": stock.seller_warehouse.seller_warehouse_id,
            "warehouse_name": stock.warehouse_name,
            "amount": int(stock.amount or 0),
            "chrt_id": stock.chrt_id,
            "nm_id": size.nm_id if size else None,
            "vendor_code": size.vendor_code if size else "",
            "title": size.title if size else "",
            "tech_size": size.tech_size if size else "",
            "wb_size": size.wb_size if size else "",
            "updated_at": stock.updated_at,
        }
        if query:
            q = query.lower()
            hay = " ".join(
                str(v or "") for v in [
                    row["warehouse_name"],
                    row["vendor_code"],
                    row["title"],
                    row["nm_id"],
                    row["chrt_id"],
                    row["tech_size"],
                    row["wb_size"],
                ]
            ).lower()
            if q not in hay:
                continue
        rows.append(row)
        total_amount += row["amount"]

    return render(
        request,
        "warehouses/fbs_stocks.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "rows": rows,
            "warehouses": warehouses,
            "warehouse_filter": warehouse_filter,
            "query": query,
            "positions_count": len(rows),
            "total_amount": total_amount,
        },
    )


@login_required
def product_cards_report(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    query = (request.GET.get("q") or "").strip()

    products_qs = Product.objects.filter(seller=seller)
    if query:
        search_filter = (
            Q(vendor_code__icontains=query)
            | Q(title__icontains=query)
            | Q(brand__icontains=query)
        )
        if query.isdigit():
            search_filter = search_filter | Q(nm_id=int(query))
        products_qs = products_qs.filter(search_filter)

    products_qs = products_qs.order_by(
        F("wb_updated_at").desc(nulls_last=True),
        "-id",
    )

    fbo_stock_map = {
        row["nm_id"]: int(row["total_qty"] or 0)
        for row in (
            WarehouseStockDetailed.objects
            .filter(seller=seller)
            .values("nm_id")
            .annotate(total_qty=Sum("quantity"))
        )
    }

    chrt_to_nm_map = {
        int(row["chrt_id"]): int(row["nm_id"])
        for row in (
            ProductCardSize.objects
            .filter(seller=seller)
            .exclude(nm_id__isnull=True)
            .values("chrt_id", "nm_id")
        )
        if row.get("chrt_id") is not None and row.get("nm_id") is not None
    }
    fbs_stock_map: dict[int, int] = {}
    for row in (
        SellerFbsStock.objects
        .filter(seller=seller)
        .values("chrt_id")
        .annotate(total_qty=Sum("amount"))
    ):
        chrt_id = row.get("chrt_id")
        if chrt_id is None:
            continue
        nm_id = chrt_to_nm_map.get(int(chrt_id))
        if nm_id is None:
            continue
        fbs_stock_map[nm_id] = int(fbs_stock_map.get(nm_id, 0) + int(row.get("total_qty") or 0))

    paginator = Paginator(products_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    page_rows = []
    for product in page_obj.object_list:
        page_rows.append(
            {
                "id": product.id,
                "nm_id": product.nm_id,
                "imt_id": product.imt_id,
                "vendor_code": product.vendor_code,
                "title": product.title or "",
                "brand": product.brand or "",
                "photo_url": product.photo_url or "",
                "weight_kg": product.weight_kg,
                "volume_liters": product.volume_liters,
                "wb_updated_at": product.wb_updated_at,
                "fbo_stock_qty": fbo_stock_map.get(product.nm_id, 0),
                "fbs_stock_qty": fbs_stock_map.get(product.nm_id, 0),
            }
        )
    page_obj.object_list = page_rows

    return render(
        request,
        "products/cards.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "query": query,
            "page_obj": page_obj,
            "total_cards_count": paginator.count,
        },
    )


@login_required
def product_glues_report(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    query = (request.GET.get("q") or "").strip()

    base_products_qs = Product.objects.filter(seller=seller, imt_id__isnull=False)
    filtered_imt_ids: list[int] | None = None
    if query:
        if query.isdigit():
            filtered_imt_ids = [int(query)]
        else:
            filtered_imt_ids = list(
                base_products_qs.filter(
                    Q(title__icontains=query)
                    | Q(vendor_code__icontains=query)
                    | Q(brand__icontains=query)
                )
                .exclude(imt_id__isnull=True)
                .values_list("imt_id", flat=True)
                .distinct()
            )

    grouped_qs = (
        base_products_qs
        .values("imt_id")
        .annotate(items_count=Count("id"))
        .filter(items_count__gt=1)
        .order_by("-items_count", "imt_id")
    )
    if filtered_imt_ids is not None:
        grouped_qs = grouped_qs.filter(imt_id__in=filtered_imt_ids)

    grouped_rows = list(grouped_qs)
    imt_ids = [row["imt_id"] for row in grouped_rows if row.get("imt_id") is not None]

    products = list(
        Product.objects
        .filter(seller=seller, imt_id__in=imt_ids)
        .order_by("imt_id", F("wb_updated_at").desc(nulls_last=True), "-id")
    )
    nm_ids = [int(p.nm_id) for p in products]

    today = timezone.localdate()
    last_30_from = today - timedelta(days=29)

    orders_by_nm = {
        int(row["nm_id"]): {
            "orders": int(row.get("orders") or 0),
            "buyouts": int(row.get("buyouts") or 0),
            "revenue": float(row.get("revenue") or 0.0),
        }
        for row in (
            Order.objects
            .filter(seller=seller, nm_id__in=nm_ids, order_date__date__gte=last_30_from, order_date__date__lte=today)
            .values("nm_id")
            .annotate(
                orders=Count("id"),
                buyouts=Count("id", filter=Q(is_buyout=True)),
                revenue=Sum("finished_price", filter=Q(is_buyout=True, finished_price__isnull=False)),
            )
        )
    }
    ad_by_nm = {
        int(row["nm_id"]): {
            "ad_spend": float(row.get("ad_spend") or 0.0),
            "ad_orders": int(row.get("ad_orders") or 0),
        }
        for row in (
            WbAdvertStatDaily.objects
            .filter(seller=seller, nm_id__in=nm_ids, stat_date__gte=last_30_from, stat_date__lte=today)
            .values("nm_id")
            .annotate(ad_spend=Sum("spend"), ad_orders=Sum("orders"))
        )
    }
    fbo_stock_by_nm = {
        int(row["nm_id"]): int(row.get("qty") or 0)
        for row in (
            WarehouseStockDetailed.objects
            .filter(seller=seller, nm_id__in=nm_ids)
            .values("nm_id")
            .annotate(qty=Sum("quantity"))
        )
    }
    chrt_to_nm_map = {
        int(row["chrt_id"]): int(row["nm_id"])
        for row in (
            ProductCardSize.objects
            .filter(seller=seller)
            .exclude(nm_id__isnull=True)
            .values("chrt_id", "nm_id")
        )
        if row.get("chrt_id") is not None and row.get("nm_id") is not None
    }
    fbs_stock_by_nm: dict[int, int] = {}
    for row in (
        SellerFbsStock.objects
        .filter(seller=seller)
        .values("chrt_id")
        .annotate(total_qty=Sum("amount"))
    ):
        chrt_id = row.get("chrt_id")
        if chrt_id is None:
            continue
        nm_id = chrt_to_nm_map.get(int(chrt_id))
        if nm_id is None:
            continue
        fbs_stock_by_nm[nm_id] = int(fbs_stock_by_nm.get(nm_id, 0) + int(row.get("total_qty") or 0))

    products_by_imt: dict[int, list[dict]] = {}
    for p in products:
        nm_id_int = int(p.nm_id)
        item_orders = orders_by_nm.get(nm_id_int, {})
        item_ad = ad_by_nm.get(nm_id_int, {})
        products_by_imt.setdefault(int(p.imt_id), []).append(
            {
                "id": p.id,
                "nm_id": p.nm_id,
                "vendor_code": p.vendor_code,
                "title": p.title or "",
                "brand": p.brand or "",
                "photo_url": p.photo_url or "",
                "orders_30d": int(item_orders.get("orders") or 0),
                "buyouts_30d": int(item_orders.get("buyouts") or 0),
                "revenue_30d": round(float(item_orders.get("revenue") or 0.0), 2),
                "ad_spend_30d": round(float(item_ad.get("ad_spend") or 0.0), 2),
                "fbo_stock_qty": int(fbo_stock_by_nm.get(nm_id_int, 0)),
                "fbs_stock_qty": int(fbs_stock_by_nm.get(nm_id_int, 0)),
            }
        )

    glue_rows = []
    for grouped in grouped_rows:
        imt_id = int(grouped["imt_id"])
        items = products_by_imt.get(imt_id, [])
        if not items:
            continue
        orders_30d = sum(item["orders_30d"] for item in items)
        buyouts_30d = sum(item["buyouts_30d"] for item in items)
        ad_spend_30d = round(sum(item["ad_spend_30d"] for item in items), 2)
        revenue_30d = round(sum(item["revenue_30d"] for item in items), 2)
        fbo_stock_qty = sum(item["fbo_stock_qty"] for item in items)
        fbs_stock_qty = sum(item["fbs_stock_qty"] for item in items)
        buyout_rate_30d = round((buyouts_30d / orders_30d) * 100.0, 2) if orders_30d > 0 else 0.0
        ad_share_30d = round((ad_spend_30d / revenue_30d) * 100.0, 2) if revenue_30d > 0 else 0.0
        glue_rows.append(
            {
                "imt_id": imt_id,
                "items_count": len(items),
                "items": items,
                "orders_30d": orders_30d,
                "buyouts_30d": buyouts_30d,
                "buyout_rate_30d": buyout_rate_30d,
                "ad_spend_30d": ad_spend_30d,
                "revenue_30d": revenue_30d,
                "ad_share_30d": ad_share_30d,
                "fbo_stock_qty": fbo_stock_qty,
                "fbs_stock_qty": fbs_stock_qty,
            }
        )

    glue_rows.sort(key=lambda row: (row["orders_30d"], row["buyouts_30d"], row["items_count"]), reverse=True)

    paginator = Paginator(glue_rows, 20)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(
        request,
        "products/glues.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "query": query,
            "page_obj": page_obj,
            "total_glues_count": len(glue_rows),
            "last_30_from": last_30_from,
            "last_30_to": today,
        },
    )


def _build_product_card_heavy_context(
    *,
    seller: SellerAccount,
    nm_id: int,
    chart_end: date,
    month_start: date,
    purchase_price: float,
    discounted_price: float,
    settings_obj: UnitEconomicsSettings,
) -> dict:
    product_orders_qs = Order.objects.filter(seller=seller, nm_id=nm_id)
    first_order_dt = product_orders_qs.aggregate(min_dt=Min("order_date")).get("min_dt")
    all_history_from = (first_order_dt.date() if first_order_dt else month_start)
    all_history_to = chart_end

    fact_profit_all = _build_fact_profit_metrics_for_product(
        seller=seller,
        nm_id=nm_id,
        date_from=all_history_from,
        date_to=all_history_to,
        purchase_price=purchase_price,
        discounted_price=discounted_price,
        settings_obj=settings_obj,
    )
    fact_profit_month = _build_fact_profit_metrics_for_product(
        seller=seller,
        nm_id=nm_id,
        date_from=month_start,
        date_to=chart_end,
        purchase_price=purchase_price,
        discounted_price=discounted_price,
        settings_obj=settings_obj,
    )

    campaigns_for_seller = list(WbAdvertCampaign.objects.filter(seller=seller).order_by("-updated_at"))
    advert_ids_from_campaign_payload = set()
    campaign_nm_ids_map: dict[int, list[int]] = {}
    for campaign in campaigns_for_seller:
        nm_ids_in_campaign = _extract_campaign_nm_ids_from_payload(campaign.raw_payload)
        if not nm_ids_in_campaign:
            continue
        try:
            advert_id_int = int(campaign.advert_id)
        except (TypeError, ValueError):
            continue
        campaign_nm_ids_map[advert_id_int] = nm_ids_in_campaign
        if int(nm_id) in nm_ids_in_campaign:
            advert_ids_from_campaign_payload.add(advert_id_int)

    related_advert_ids = sorted(advert_ids_from_campaign_payload)
    spend_by_advert_for_block = _build_campaign_spend_totals(
        seller=seller,
        advert_ids=related_advert_ids,
        date_from=month_start,
        date_to=chart_end,
    )
    participant_nm_ids_for_block: set[int] = set()
    for advert_id_int in related_advert_ids:
        participant_nm_ids_for_block.update(campaign_nm_ids_map.get(advert_id_int, []))
    sales_base_by_nm_for_block = _build_sales_base_by_nm(
        seller=seller,
        date_from=month_start,
        date_to=chart_end,
        nm_ids=participant_nm_ids_for_block,
    )
    campaigns_by_id = {
        int(c.advert_id): c
        for c in campaigns_for_seller
        if c.advert_id is not None
    }

    ad_campaign_rows = []
    total_ad_spend_related = 0.0
    total_ad_orders_related = 0
    total_ad_clicks_related = 0
    total_ad_views_related = 0
    for advert_id in related_advert_ids:
        campaign = campaigns_by_id.get(int(advert_id))
        nm_ids_in_campaign = campaign_nm_ids_map.get(int(advert_id), [])
        campaign_raw_payload = campaign.raw_payload if campaign and isinstance(campaign.raw_payload, dict) else {}
        campaign_name_value = (
            (campaign.campaign_name if campaign else None)
            or (campaign_raw_payload.get("name") if campaign_raw_payload else None)
            or (campaign_raw_payload.get("advertName") if campaign_raw_payload else None)
            or (
                (campaign_raw_payload.get("settings") or {}).get("name")
                if isinstance(campaign_raw_payload.get("settings"), dict)
                else None
            )
            or "-"
        )
        campaign_total_spend = float(spend_by_advert_for_block.get(int(advert_id), 0.0))
        spend_sum, spend_is_allocated = _allocate_campaign_spend_for_nm(
            target_nm_id=int(nm_id),
            campaign_nm_ids=nm_ids_in_campaign,
            campaign_total_spend=campaign_total_spend,
            sale_base_by_nm=sales_base_by_nm_for_block,
        )
        stats_qs = WbAdvertStatDaily.objects.filter(
            seller=seller,
            advert_id=int(advert_id),
            stat_date__gte=month_start,
            stat_date__lte=chart_end,
        )
        first_stat_date = stats_qs.aggregate(v=Min("stat_date")).get("v")
        last_stat_date = stats_qs.aggregate(v=Max("stat_date")).get("v")
        day_totals: dict[date, dict[str, float]] = {}
        for stat_row in stats_qs.only("stat_date", "views", "clicks", "orders", "add_to_cart", "raw_payload"):
            stat_day = stat_row.stat_date
            if stat_day is None:
                continue
            payload = stat_row.raw_payload if isinstance(stat_row.raw_payload, dict) else {}
            day_payload = payload.get("day") if isinstance(payload.get("day"), dict) else {}

            views_value = _to_float_or_default(stat_row.views, _to_float_or_default(day_payload.get("views"), 0.0))
            clicks_value = _to_float_or_default(stat_row.clicks, _to_float_or_default(day_payload.get("clicks"), 0.0))
            orders_value = _to_float_or_default(stat_row.orders, _to_float_or_default(day_payload.get("orders"), 0.0))
            atc_value = _to_float_or_default(stat_row.add_to_cart, _to_float_or_default(day_payload.get("atbs"), 0.0))

            bucket = day_totals.setdefault(
                stat_day,
                {"views": 0.0, "clicks": 0.0, "orders": 0.0, "add_to_cart": 0.0},
            )
            bucket["views"] = max(bucket["views"], views_value)
            bucket["clicks"] = max(bucket["clicks"], clicks_value)
            bucket["orders"] = max(bucket["orders"], orders_value)
            bucket["add_to_cart"] = max(bucket["add_to_cart"], atc_value)

        days_count = len(day_totals)
        views_sum = int(sum(values["views"] for values in day_totals.values()))
        clicks_sum = int(sum(values["clicks"] for values in day_totals.values()))
        orders_sum = int(sum(values["orders"] for values in day_totals.values()))
        atc_sum = int(sum(values["add_to_cart"] for values in day_totals.values()))

        campaign_status = int(_to_float_or_default((campaign.status if campaign else None), 0.0))
        if (
            campaign_status == 7
            and campaign_total_spend <= 0
            and days_count == 0
            and views_sum == 0
            and clicks_sum == 0
            and orders_sum == 0
            and atc_sum == 0
        ):
            continue

        ctr_percent = round((clicks_sum / views_sum) * 100.0, 2) if views_sum > 0 else 0.0
        cpc = round((spend_sum / clicks_sum), 2) if clicks_sum > 0 else 0.0
        advert_type_value = campaign.advert_type if campaign else None
        status_value = campaign.status if campaign else None
        status_label, status_css = _advert_status_meta(status_value)

        total_ad_spend_related += spend_sum
        total_ad_orders_related += orders_sum
        total_ad_clicks_related += clicks_sum
        total_ad_views_related += views_sum

        ad_campaign_rows.append(
            {
                "advert_id": int(advert_id),
                "campaign_name": campaign_name_value,
                "status": status_value,
                "status_label": status_label,
                "status_css": status_css,
                "advert_type": advert_type_value,
                "advert_type_label": _advert_type_label(advert_type_value),
                "create_time": campaign.create_time if campaign else None,
                "start_time": campaign.start_time if campaign else None,
                "end_time": campaign.end_time if campaign else None,
                "daily_budget": float(campaign.daily_budget or 0.0) if campaign and campaign.daily_budget is not None else 0.0,
                "nm_ids_in_campaign": nm_ids_in_campaign,
                "participants_count": len(nm_ids_in_campaign),
                "spend_allocated": bool(spend_is_allocated),
                "campaign_total_spend": campaign_total_spend,
                "first_stat_date": first_stat_date,
                "last_stat_date": last_stat_date,
                "days_count": days_count,
                "spend_sum": spend_sum,
                "views_sum": views_sum,
                "clicks_sum": clicks_sum,
                "orders_sum": orders_sum,
                "add_to_cart_sum": atc_sum,
                "ctr_percent": ctr_percent,
                "cpc": cpc,
            }
        )
    ad_campaign_rows.sort(
        key=lambda row: (
            row.get("create_time") or row.get("start_time") or row.get("last_stat_date") or date.min,
            row.get("advert_id") or 0,
        ),
        reverse=True,
    )

    return {
        "fact_profit_all": fact_profit_all,
        "fact_profit_month": fact_profit_month,
        "ad_campaign_rows": ad_campaign_rows,
        "ad_related_campaigns_count": len(ad_campaign_rows),
        "ad_total_spend_related": total_ad_spend_related,
        "ad_total_orders_related": total_ad_orders_related,
        "ad_total_clicks_related": total_ad_clicks_related,
        "ad_total_views_related": total_ad_views_related,
    }


@login_required
def product_card_detail(request, product_id: int):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    product = Product.objects.filter(seller=seller, id=product_id).first()
    if not product:
        messages.error(request, "Карточка товара не найдена.")
        return redirect("product_cards_report")

    nm_id = product.nm_id

    product_orders_qs = Order.objects.filter(seller=seller, nm_id=nm_id)
    # Для детализации карточки и рекламного блока используем "сегодня" как правую границу периода.
    # Иначе при отсутствии новых заказов за последние дни реклама визуально "обрезается" по max(order_date).
    chart_end = timezone.localdate()
    chart_start = chart_end - timedelta(days=13)

    daily_rows = (
        product_orders_qs
        .filter(order_date__date__gte=chart_start, order_date__date__lte=chart_end)
        .annotate(day=TruncDate("order_date"))
        .values("day")
        .annotate(
            total_count=Count("id"),
            sold_count=Count("id", filter=Q(is_buyout=True)),
            canceled_count=Count("id", filter=Q(is_cancel=True) | Q(is_return=True)),
        )
        .order_by("day")
    )
    daily_map = {row["day"]: row for row in daily_rows}

    sales_points = []
    day_cursor = chart_start
    while day_cursor <= chart_end:
        row = daily_map.get(day_cursor)
        sales_points.append(
            {
                "date": day_cursor.isoformat(),
                "label": day_cursor.strftime("%d.%m"),
                "total_orders": int((row or {}).get("total_count") or 0),
                "sold_orders": int((row or {}).get("sold_count") or 0),
                "canceled_orders": int((row or {}).get("canceled_count") or 0),
            }
        )
        day_cursor += timedelta(days=1)

    fbo_rows = list(
        WarehouseStockDetailed.objects
        .filter(seller=seller, nm_id=nm_id)
        .values("warehouse_name")
        .annotate(quantity=Sum("quantity"))
        .order_by("-quantity", "warehouse_name")
    )
    fbo_total = int(sum(int(row.get("quantity") or 0) for row in fbo_rows))
    fbo_distribution = [
        {"warehouse_name": row.get("warehouse_name") or "-", "quantity": int(row.get("quantity") or 0)}
        for row in fbo_rows
        if int(row.get("quantity") or 0) > 0
    ]

    chrt_ids = list(
        ProductCardSize.objects
        .filter(seller=seller, nm_id=nm_id)
        .values_list("chrt_id", flat=True)
    )
    fbs_distribution = []
    fbs_total = 0
    if chrt_ids:
        fbs_rows = list(
            SellerFbsStock.objects
            .filter(seller=seller, chrt_id__in=chrt_ids)
            .values("warehouse_name")
            .annotate(quantity=Sum("amount"))
            .order_by("-quantity", "warehouse_name")
        )
        fbs_distribution = [
            {"warehouse_name": row.get("warehouse_name") or "-", "quantity": int(row.get("quantity") or 0)}
            for row in fbs_rows
            if int(row.get("quantity") or 0) > 0
        ]
        fbs_total = int(sum(int(row.get("quantity") or 0) for row in fbs_rows))

    month_start = chart_end - timedelta(days=29)
    monthly_orders_qs = product_orders_qs.filter(
        order_date__date__gte=month_start,
        order_date__date__lte=chart_end,
    )
    monthly_stats = monthly_orders_qs.aggregate(
        total_orders=Count("id"),
        sold_orders=Count("id", filter=Q(is_buyout=True)),
    )
    monthly_total_orders = int(monthly_stats.get("total_orders") or 0)
    monthly_sold_orders = int(monthly_stats.get("sold_orders") or 0)
    monthly_buyout_percent = round((monthly_sold_orders / monthly_total_orders) * 100, 2) if monthly_total_orders else 0.0
    monthly_avg_orders_per_day = round(monthly_total_orders / 30.0, 2)

    all_time_stats = product_orders_qs.aggregate(
        total_orders=Count("id"),
        sold_orders=Count("id", filter=Q(is_buyout=True)),
    )
    all_time_total_orders = int(all_time_stats.get("total_orders") or 0)
    all_time_sold_orders = int(all_time_stats.get("sold_orders") or 0)
    buyout_percent_all_time = round((all_time_sold_orders / all_time_total_orders) * 100.0, 2) if all_time_total_orders else 0.0

    sales_14d_end = chart_end
    sales_14d_start = sales_14d_end - timedelta(days=13)
    sales_14d_total = int(
        product_orders_qs.filter(
            order_date__date__gte=sales_14d_start,
            order_date__date__lte=sales_14d_end,
        ).count()
    )
    avg_sales_per_day_14d = round(sales_14d_total / 14.0, 4)

    discounted_price_from_prices = (
        ProductSizePrice.objects
        .filter(seller=seller, nm_id=nm_id)
        .exclude(discounted_price__isnull=True)
        .order_by("-updated_at", "-id")
        .values_list("discounted_price", flat=True)
        .first()
    )
    default_sale_price = round(_to_float_or_default(discounted_price_from_prices, 0.0), 2)
    default_purchase_price = round(_to_float_or_default(product.purchase_price, 0.0), 2)

    settings_obj = _get_or_create_unit_economics_settings(seller)

    today = timezone.localdate()
    acceptance_rows = list(
        WbAcceptanceCoefficient.objects
        .filter(seller=seller)
        .exclude(warehouse_name__isnull=True)
        .exclude(warehouse_name="")
        .order_by("warehouse_name", "box_type_id", "coeff_date")
    )
    grouped_coeffs: dict[tuple[str, int], list[WbAcceptanceCoefficient]] = {}
    for row in acceptance_rows:
        key = ((row.warehouse_name or "").strip(), int(row.box_type_id or 0))
        grouped_coeffs.setdefault(key, []).append(row)

    warehouse_coeff_options = []
    warehouse_names_sorted = sorted({(r.warehouse_name or "").strip() for r in acceptance_rows if (r.warehouse_name or "").strip()})
    for wh_name in warehouse_names_sorted:
        option = {"warehouse_name": wh_name, "box": {}, "mono": {}}
        for box_type_id, target_key in ((2, "box"), (5, "mono")):
            rows_for_key = grouped_coeffs.get((wh_name, box_type_id), [])
            chosen = None
            future = sorted(
                [r for r in rows_for_key if r.coeff_date and r.coeff_date >= today],
                key=lambda r: r.coeff_date,
            )
            available_future = [
                r for r in future
                if bool(r.allow_unload) and r.coefficient is not None and float(r.coefficient) >= 0
            ]
            next_available = available_future[0] if available_future else None
            if next_available:
                chosen = next_available
            elif future:
                chosen = future[0]
            elif rows_for_key:
                chosen = sorted(rows_for_key, key=lambda r: r.coeff_date or date.min, reverse=True)[0]
            if chosen:
                is_available = bool(chosen.allow_unload) and chosen.coefficient is not None and float(chosen.coefficient) >= 0
                option[target_key] = {
                    "coeff_date": chosen.coeff_date.isoformat() if chosen.coeff_date else None,
                    "delivery_coef": float(chosen.delivery_coef or 0.0),
                    "delivery_base_liter": float(chosen.delivery_base_liter or 0.0),
                    "delivery_additional_liter": float(chosen.delivery_additional_liter or 0.0),
                    "storage_base_liter": float(chosen.storage_base_liter or 0.0),
                    "storage_additional_liter": float(chosen.storage_additional_liter or 0.0),
                    "acceptance_coef": float(chosen.coefficient or 0.0),
                    "allow_unload": bool(chosen.allow_unload),
                    "is_available": is_available,
                    "next_available_date": next_available.coeff_date.isoformat() if next_available and next_available.coeff_date else None,
                }
            else:
                option[target_key] = {
                    "coeff_date": None,
                    "delivery_coef": 0.0,
                    "delivery_base_liter": 0.0,
                    "delivery_additional_liter": 0.0,
                    "storage_base_liter": 0.0,
                    "storage_additional_liter": 0.0,
                    "acceptance_coef": 0.0,
                    "allow_unload": False,
                    "is_available": False,
                    "next_available_date": None,
                }
        warehouse_coeff_options.append(option)

    latest_theoretical_il = 1.0
    latest_theoretical_irp_percent = 0.0
    il_trend = get_theoretical_localization_index_trend_last_full_weeks(seller, weeks=1)
    if il_trend.get("points"):
        latest_theoretical_il = float(il_trend["points"][-1].get("theoretical_index") or 1.0)
    irp_trend = get_theoretical_irp_trend_last_full_weeks(seller, weeks=1)
    if irp_trend.get("points"):
        latest_theoretical_irp_percent = float(irp_trend["points"][-1].get("theoretical_irp_percent") or 0.0)

    commission_percent_fbo = _resolve_model_commission_percent(
        seller=seller,
        product=product,
        model_type=UNIT_MODEL_FBO,
    )
    commission_percent_fbs = _resolve_model_commission_percent(
        seller=seller,
        product=product,
        model_type=UNIT_MODEL_FBS,
    )

    marketplace_region_options = []
    marketplace_tariffs = list(
        WbWarehouseTariff.objects
        .filter(seller=seller, warehouse_name__startswith="Маркетплейс:")
        .exclude(Q(warehouse_name__icontains="СГТ") | Q(warehouse_name__icontains="SGT"))
        .order_by("-tariff_date", "-updated_at")
    )
    region_rows: dict[str, list[WbWarehouseTariff]] = {}
    for row in marketplace_tariffs:
        region_name = (row.geo_name or "").strip()
        if not region_name:
            region_name = (row.warehouse_name or "").replace("Маркетплейс:", "").strip()
        if not region_name:
            continue
        region_name_lc = region_name.lower()
        if "сгт" in region_name_lc or "sgt" in region_name_lc:
            continue
        region_rows.setdefault(region_name.lower(), []).append(row)

    for region_key, rows in region_rows.items():
        chosen = None
        for row in rows:
            coef = _to_float_or_default(
                row.box_delivery_marketplace_coef_expr,
                _to_float_or_default(row.box_delivery_coef_expr, 0.0),
            )
            if coef > 0:
                chosen = row
                break
        if chosen is None and rows:
            chosen = rows[0]
        if chosen is None:
            continue
        region_name = (chosen.geo_name or "").strip() or (chosen.warehouse_name or "").replace("Маркетплейс:", "").strip()
        marketplace_region_options.append(
            {
                "region_name": region_name or region_key,
                "delivery_coef": _to_float_or_default(
                    chosen.box_delivery_marketplace_coef_expr,
                    _to_float_or_default(chosen.box_delivery_coef_expr, 0.0),
                ),
                "delivery_base_liter": _to_float_or_default(
                    chosen.box_delivery_marketplace_base,
                    _to_float_or_default(chosen.box_delivery_base, 0.0),
                ),
                "delivery_additional_liter": _to_float_or_default(
                    chosen.box_delivery_marketplace_liter,
                    _to_float_or_default(chosen.box_delivery_liter, 0.0),
                ),
                "tariff_date": chosen.tariff_date.isoformat() if chosen.tariff_date else None,
            }
        )
    marketplace_region_options.sort(key=lambda x: x.get("region_name") or "")

    default_days_to_sell_batch = 30
    if avg_sales_per_day_14d > 0:
        default_days_to_sell_batch = max(1, round(100 / avg_sales_per_day_14d))

    saved_unit_calcs_qs = (
        ProductUnitEconomicsCalculation.objects
        .filter(seller=seller, product=product)
        .order_by("-calculated_at")
    )
    saved_unit_calc_payload = {}
    saved_unit_calcs_payload: dict[str, dict] = {}
    for row in saved_unit_calcs_qs:
        if not isinstance(row.result_data, dict):
            continue
        model_type = _normalize_unit_model_type(
            row.model_type
            or (row.input_data.get("model_type") if isinstance(row.input_data, dict) else None)
        )
        payload = {
            "result": row.result_data,
            "input_data": row.input_data if isinstance(row.input_data, dict) else {},
            "calculated_at": row.calculated_at.isoformat() if row.calculated_at else None,
        }
        if not saved_unit_calc_payload:
            saved_unit_calc_payload = payload
        if model_type not in saved_unit_calcs_payload:
            saved_unit_calcs_payload[model_type] = payload

    return render(
        request,
        "products/card_detail.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "product": product,
            "chart_start": chart_start,
            "chart_end": chart_end,
            "sales_points_json": sales_points,
            "month_start": month_start,
            "monthly_total_orders": monthly_total_orders,
            "monthly_buyout_percent": monthly_buyout_percent,
            "monthly_avg_orders_per_day": monthly_avg_orders_per_day,
            "fbo_total": fbo_total,
            "fbs_total": fbs_total,
            "fbo_distribution": fbo_distribution,
            "fbs_distribution": fbs_distribution,
            "unit_settings": settings_obj,
            "unit_default_sale_price": default_sale_price,
            "unit_default_purchase_price": default_purchase_price,
            "unit_buyout_percent_all_time": buyout_percent_all_time,
            "unit_avg_sales_per_day_14d": avg_sales_per_day_14d,
            "unit_latest_theoretical_il": latest_theoretical_il,
            "unit_latest_theoretical_irp_percent": latest_theoretical_irp_percent,
            "unit_default_commission_percent_fbo": commission_percent_fbo,
            "unit_default_commission_percent_fbs": commission_percent_fbs,
            "unit_default_days_to_sell_batch": default_days_to_sell_batch,
            "unit_warehouse_coeff_options_json": warehouse_coeff_options,
            "unit_marketplace_region_options_json": marketplace_region_options,
            "unit_saved_calc_json": saved_unit_calc_payload,
            "unit_saved_calcs_json": saved_unit_calcs_payload,
            "unit_has_saved_calc": bool(saved_unit_calcs_payload),
        },
    )


@login_required
@require_GET
def product_card_detail_heavy_api(request, product_id: int):
    seller = _get_or_create_seller_for_user(request.user)
    product = Product.objects.filter(seller=seller, id=product_id).first()
    if not product:
        return JsonResponse({"ok": False, "error": "Карточка товара не найдена."}, status=404)

    nm_id = int(product.nm_id)
    product_orders_qs = Order.objects.filter(seller=seller, nm_id=nm_id)
    chart_end = timezone.localdate()
    month_start = chart_end - timedelta(days=29)

    discounted_price_from_prices = (
        ProductSizePrice.objects
        .filter(seller=seller, nm_id=nm_id)
        .exclude(discounted_price__isnull=True)
        .order_by("-updated_at", "-id")
        .values_list("discounted_price", flat=True)
        .first()
    )
    default_sale_price = round(_to_float_or_default(discounted_price_from_prices, 0.0), 2)
    default_purchase_price = round(_to_float_or_default(product.purchase_price, 0.0), 2)
    settings_obj = _get_or_create_unit_economics_settings(seller)
    saved_unit_calc_exists = ProductUnitEconomicsCalculation.objects.filter(seller=seller, product=product).exists()

    heavy_context = _build_product_card_heavy_context(
        seller=seller,
        nm_id=nm_id,
        chart_end=chart_end,
        month_start=month_start,
        purchase_price=default_purchase_price,
        discounted_price=default_sale_price,
        settings_obj=settings_obj,
    )

    html = render_to_string(
        "products/partials/card_detail_heavy_sections.html",
        {
            "unit_has_saved_calc": saved_unit_calc_exists,
            **heavy_context,
        },
        request=request,
    )
    return JsonResponse(
        {
            "ok": True,
            "html": html,
            "fact_profit_month": heavy_context["fact_profit_month"],
        }
    )


@login_required
@require_POST
def product_unit_economics_settings_api(request, product_id: int):
    seller = _get_or_create_seller_for_user(request.user)
    if not Product.objects.filter(seller=seller, id=product_id).exists():
        return JsonResponse({"error": "Карточка товара не найдена."}, status=404)

    settings_obj = _get_or_create_unit_economics_settings(seller)
    settings_obj.assumed_spp_percent = _safe_percent(request.POST.get("assumed_spp_percent"), settings_obj.assumed_spp_percent)
    settings_obj.drr_percent = _safe_percent(request.POST.get("drr_percent"), settings_obj.drr_percent)
    settings_obj.defect_percent = _safe_percent(request.POST.get("defect_percent"), settings_obj.defect_percent)
    settings_obj.acquiring_percent = _safe_percent(request.POST.get("acquiring_percent"), settings_obj.acquiring_percent)
    settings_obj.acceptance_cost_per_liter = max(
        0.0,
        _to_float_or_default(request.POST.get("acceptance_cost_per_liter"), settings_obj.acceptance_cost_per_liter),
    )
    settings_obj.fulfillment_cost_per_order = max(0.0, _to_float_or_default(request.POST.get("fulfillment_cost_per_order"), settings_obj.fulfillment_cost_per_order))
    settings_obj.fbo_fulfillment_cost_per_order = max(
        0.0,
        _to_float_or_default(
            request.POST.get("fbo_fulfillment_cost_per_order"),
            _to_float_or_default(settings_obj.fbo_fulfillment_cost_per_order, settings_obj.fulfillment_cost_per_order),
        ),
    )
    settings_obj.fbs_fulfillment_cost_per_order = max(
        0.0,
        _to_float_or_default(
            request.POST.get("fbs_fulfillment_cost_per_order"),
            _to_float_or_default(settings_obj.fbs_fulfillment_cost_per_order, settings_obj.fulfillment_cost_per_order),
        ),
    )
    settings_obj.usn_percent = _safe_percent(request.POST.get("usn_percent"), settings_obj.usn_percent)
    settings_obj.vat_percent = _safe_percent(request.POST.get("vat_percent"), settings_obj.vat_percent)
    settings_obj.save()

    return JsonResponse(
        {
            "ok": True,
            "settings": {
                "assumed_spp_percent": settings_obj.assumed_spp_percent,
                "drr_percent": settings_obj.drr_percent,
                "defect_percent": settings_obj.defect_percent,
                "acquiring_percent": settings_obj.acquiring_percent,
                "acceptance_cost_per_liter": settings_obj.acceptance_cost_per_liter,
                "fulfillment_cost_per_order": settings_obj.fulfillment_cost_per_order,
                "fbo_fulfillment_cost_per_order": settings_obj.fbo_fulfillment_cost_per_order,
                "fbs_fulfillment_cost_per_order": settings_obj.fbs_fulfillment_cost_per_order,
                "usn_percent": settings_obj.usn_percent,
                "vat_percent": settings_obj.vat_percent,
            },
        }
    )


@login_required
@require_POST
def product_unit_economics_calculate_api(request, product_id: int):
    seller = _get_or_create_seller_for_user(request.user)
    product = Product.objects.filter(seller=seller, id=product_id).first()
    if not product:
        return JsonResponse({"error": "Карточка товара не найдена."}, status=404)
    settings_obj = _get_or_create_unit_economics_settings(seller)
    preview_mode = (request.POST.get("preview") or "").strip().lower() in {"1", "true", "yes", "on"}
    model_type = _normalize_unit_model_type(request.POST.get("model_type"))
    model_labels = _unit_model_labels(model_type)
    default_commission_percent = _resolve_model_commission_percent(
        seller=seller,
        product=product,
        model_type=model_type,
    )
    default_fulfillment_cost = _resolve_model_fulfillment_cost(settings_obj, model_type)

    sale_price = max(0.0, _to_float_or_default(request.POST.get("sale_price"), 0.0))
    purchase_price = max(0.0, _to_float_or_default(request.POST.get("purchase_price"), 0.0))
    spp_percent = _safe_percent(request.POST.get("spp_percent"), 25.0)
    defect_percent = _safe_percent(request.POST.get("defect_percent"), 1.0)
    buyout_percent = _safe_percent(request.POST.get("buyout_percent"), 100.0)
    drr_percent = _safe_percent(request.POST.get("drr_percent"), 10.0)
    fulfillment_cost = max(0.0, _to_float_or_default(request.POST.get("fulfillment_cost"), default_fulfillment_cost))
    usn_percent = _safe_percent(request.POST.get("usn_percent"), 6.0)
    vat_percent = _safe_percent(request.POST.get("vat_percent"), 0.0)
    acquiring_percent = _safe_percent(request.POST.get("acquiring_percent"), 0.0)
    batch_qty = max(1.0, _to_float_or_default(request.POST.get("batch_qty"), 1.0))
    commission_percent = _safe_percent(request.POST.get("commission_percent"), default_commission_percent)
    delivery_coef_expr = max(0.0, _to_float_or_default(request.POST.get("delivery_coef_expr"), 0.0))
    extra_cost = max(0.0, _to_float_or_default(request.POST.get("extra_cost"), 0.0))
    days_to_sell = max(1.0, _to_float_or_default(request.POST.get("days_to_sell"), 30.0))
    localization_index = max(0.0, _to_float_or_default(request.POST.get("localization_index"), 1.0))
    irp_percent = max(0.0, _to_float_or_default(request.POST.get("irp_percent"), 0.0))
    acceptance_coef = max(0.0, _to_float_or_default(request.POST.get("acceptance_coef"), 0.0))
    box_type = (request.POST.get("box_type") or "box").strip().lower()
    storage_base_liter = max(0.0, _to_float_or_default(request.POST.get("storage_base_liter"), 0.0))
    storage_additional_liter = max(0.0, _to_float_or_default(request.POST.get("storage_additional_liter"), 0.0))
    warehouse_name = (request.POST.get("warehouse_name") or "").strip()
    region_name = (request.POST.get("region_name") or "").strip()
    if model_type == UNIT_MODEL_FBS and region_name:
        warehouse_name = region_name
    if model_type == UNIT_MODEL_FBS:
        # Для FBS ИЛ/ИРП в юнитке не участвуют в расчете.
        localization_index = 1.0
        irp_percent = 0.0

    volume = max(0.0, _to_float_or_default(request.POST.get("volume_liters"), product.volume_liters or DEFAULT_LOGISTICS_VOLUME_LITERS))
    if volume <= 0:
        volume = DEFAULT_LOGISTICS_VOLUME_LITERS

    # Сохраняем закупочную цену только в обычном режиме (не preview),
    # чтобы live-калькулятор не перезаписывал карточку.
    if not preview_mode:
        if product.purchase_price is None or abs(float(product.purchase_price) - purchase_price) > 1e-9:
            product.purchase_price = purchase_price
            product.save(update_fields=["purchase_price"])

    drr_cost = sale_price * (drr_percent / 100.0)
    defect_cost = purchase_price * (defect_percent / 100.0)
    acquiring_cost = sale_price * (acquiring_percent / 100.0)
    tax_base = sale_price * (1.0 - (spp_percent / 100.0))
    usn_cost = tax_base * (usn_percent / 100.0)
    vat_cost = tax_base * (vat_percent / 100.0)
    commission_cost = sale_price * (commission_percent / 100.0)

    logistics_base = calculate_theoretical_order_logistics(
        volume_liters=volume,
        api_coef_expr=delivery_coef_expr,
        fixed_delivery_coef=None,
        use_dlv_prc=False,
        as_of_date=max(timezone.localdate(), LOGISTICS_IRP_SWITCH_DATE),
        retail_price_before_discount=sale_price,
        irp_index=(irp_percent / 100.0),
    )
    delivery_cost = logistics_base * localization_index

    buyout_fraction = max(0.0001, buyout_percent / 100.0)
    non_buyout_logistics_cost = delivery_cost * ((1.0 - buyout_fraction) / buyout_fraction)

    if model_type == UNIT_MODEL_FBS:
        storage_base_liter = 0.0
        storage_additional_liter = 0.0
        storage_cost = 0.0
    else:
        if box_type == "mono":
            storage_per_day = storage_base_liter
        else:
            storage_per_day = storage_base_liter + storage_additional_liter * max(volume - 1.0, 0.0)
        storage_cost = storage_per_day * days_to_sell

    acceptance_cost_per_liter = max(
        0.0,
        _to_float_or_default(
            request.POST.get("acceptance_cost_per_liter"),
            float(settings_obj.acceptance_cost_per_liter or 0.0),
        ),
    )
    acceptance_cost = acceptance_cost_per_liter * volume * max(0.0, acceptance_coef)
    acceptance_note = (
        f"{model_labels['acceptance']}: {acceptance_cost_per_liter:.2f} ₽/л × объем {volume:.3f} л × коэффициент {max(0.0, acceptance_coef):.2f}."
    )
    _ = batch_qty  # batch_qty хранится для будущего расширения формулы.
    _ = acceptance_coef

    revenue_after_purchase = sale_price - purchase_price
    total_costs = (
        drr_cost
        + defect_cost
        + acquiring_cost
        + fulfillment_cost
        + extra_cost
        + delivery_cost
        + usn_cost
        + vat_cost
        + acceptance_cost
        + commission_cost
        + storage_cost
        + non_buyout_logistics_cost
    )
    net_profit = revenue_after_purchase - total_costs

    segments = [
        {"key": "purchase", "label": "Закупочная цена", "value": round(purchase_price, 2), "kind": "expense"},
        {"key": "drr", "label": "ДРР", "value": round(drr_cost, 2), "kind": "expense"},
        {"key": "defect", "label": "Брак", "value": round(defect_cost, 2), "kind": "expense"},
        {"key": "acquiring", "label": "Эквайринг", "value": round(acquiring_cost, 2), "kind": "expense"},
        {"key": "fulfillment", "label": model_labels["fulfillment"], "value": round(fulfillment_cost, 2), "kind": "expense"},
        {"key": "extra", "label": "Доп. расходы", "value": round(extra_cost, 2), "kind": "expense"},
        {"key": "delivery", "label": model_labels["delivery"], "value": round(delivery_cost, 2), "kind": "expense"},
        {"key": "non_buyout", "label": model_labels["non_buyout"], "value": round(non_buyout_logistics_cost, 2), "kind": "expense"},
        {"key": "commission", "label": "Комиссия WB", "value": round(commission_cost, 2), "kind": "expense"},
        {"key": "usn", "label": "Налог УСН", "value": round(usn_cost, 2), "kind": "expense"},
        {"key": "vat", "label": "НДС", "value": round(vat_cost, 2), "kind": "expense"},
        {"key": "storage", "label": model_labels["storage"], "value": round(storage_cost, 2), "kind": "expense"},
        {"key": "acceptance", "label": model_labels["acceptance"], "value": round(acceptance_cost, 2), "kind": "expense"},
        {"key": "net_profit", "label": "Чистая прибыль", "value": round(net_profit, 2), "kind": ("profit" if net_profit >= 0 else "loss")},
    ]

    result_payload = {
        "net_profit": round(net_profit, 2),
        "segments": segments,
        "breakdown": {
            "model_type": model_type,
            "model_labels": model_labels,
            "sale_price": round(sale_price, 2),
            "spp_percent": round(spp_percent, 4),
            "purchase_price": round(purchase_price, 2),
            "drr_cost": round(drr_cost, 2),
            "defect_cost": round(defect_cost, 2),
            "acquiring_cost": round(acquiring_cost, 2),
            "fulfillment_cost": round(fulfillment_cost, 2),
            "extra_cost": round(extra_cost, 2),
            "delivery_cost": round(delivery_cost, 2),
            "non_buyout_logistics_cost": round(non_buyout_logistics_cost, 2),
            "commission_cost": round(commission_cost, 2),
            "usn_cost": round(usn_cost, 2),
            "vat_cost": round(vat_cost, 2),
            "storage_cost": round(storage_cost, 2),
            "acceptance_cost": round(acceptance_cost, 2),
            "acceptance_cost_per_liter": round(acceptance_cost_per_liter, 4),
            "localization_index": round(localization_index, 4),
            "irp_percent": round(irp_percent, 4),
            "acceptance_note": acceptance_note,
            "warehouse_name": warehouse_name,
            "region_name": region_name,
            "delivery_coef_expr": round(delivery_coef_expr, 4),
            "storage_base_liter": round(storage_base_liter, 4),
            "storage_additional_liter": round(storage_additional_liter, 4),
        },
    }

    input_payload = {
        "model_type": model_type,
        "sale_price": round(sale_price, 2),
        "purchase_price": round(purchase_price, 2),
        "spp_percent": round(spp_percent, 4),
        "defect_percent": round(defect_percent, 4),
        "buyout_percent": round(buyout_percent, 4),
        "drr_percent": round(drr_percent, 4),
        "fulfillment_cost": round(fulfillment_cost, 2),
        "usn_percent": round(usn_percent, 4),
        "vat_percent": round(vat_percent, 4),
        "acquiring_percent": round(acquiring_percent, 4),
        "batch_qty": round(batch_qty, 4),
        "commission_percent": round(commission_percent, 4),
        "delivery_coef_expr": round(delivery_coef_expr, 4),
        "warehouse_name": warehouse_name,
        "region_name": region_name,
        "extra_cost": round(extra_cost, 2),
        "days_to_sell": round(days_to_sell, 2),
        "localization_index": round(localization_index, 6),
        "irp_percent": round(irp_percent, 6),
        "acceptance_coef": round(acceptance_coef, 4),
        "acceptance_cost_per_liter": round(acceptance_cost_per_liter, 4),
        "box_type": box_type,
        "storage_base_liter": round(storage_base_liter, 4),
        "storage_additional_liter": round(storage_additional_liter, 4),
        "volume_liters": round(volume, 6),
    }

    if not preview_mode:
        ProductUnitEconomicsCalculation.objects.update_or_create(
            seller=seller,
            product=product,
            model_type=model_type,
            defaults={
                "model_type": model_type,
                "input_data": input_payload,
                "result_data": result_payload,
                "net_profit": result_payload["net_profit"],
            },
        )

    return JsonResponse(
        {
            "ok": True,
            "result": result_payload,
        }
    )


@login_required
@require_POST
def create_feedback_api(request):
    message = (request.POST.get("message") or "").strip()
    category = (request.POST.get("category") or TesterFeedback.CATEGORY_BUG).strip()
    priority = (request.POST.get("priority") or TesterFeedback.PRIORITY_MEDIUM).strip()
    page_url = (request.POST.get("page_url") or "").strip()
    include_context = (request.POST.get("include_context") or "1").strip() in {"1", "true", "True", "on"}
    raw_context = (request.POST.get("context_json") or "").strip()

    if not message or len(message) < 5:
        return JsonResponse({"error": "Комментарий слишком короткий (минимум 5 символов)."}, status=400)

    valid_categories = {value for value, _ in TesterFeedback.CATEGORY_CHOICES}
    valid_priorities = {value for value, _ in TesterFeedback.PRIORITY_CHOICES}
    if category not in valid_categories:
        category = TesterFeedback.CATEGORY_BUG
    if priority not in valid_priorities:
        priority = TesterFeedback.PRIORITY_MEDIUM

    context_json = {}
    if include_context:
        context_json = {
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "referer": request.META.get("HTTP_REFERER", ""),
            "path": request.path,
            "posted_at": timezone.now().isoformat(),
        }
        if raw_context:
            try:
                parsed = json.loads(raw_context)
                if isinstance(parsed, dict):
                    context_json.update(parsed)
            except json.JSONDecodeError:
                context_json["raw_context"] = raw_context[:1000]

    seller = _get_seller_for_user(request.user)

    try:
        item = TesterFeedback.objects.create(
            user=request.user,
            seller=seller,
            page_url=page_url,
            category=category,
            priority=priority,
            message=message,
            include_context=include_context,
            context_json=context_json,
        )
        return JsonResponse({"ok": True, "ticket_id": item.id}, status=201)
    except Exception as exc:
        _log_app_error(
            source="feedback.create_api",
            message=f"Не удалось сохранить фидбек: {exc}",
            user=request.user,
            seller=seller,
            path=request.path,
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Не удалось сохранить сообщение. Попробуйте ещё раз."}, status=500)


SUPPORT_STATUS_META = {
    SupportThread.STATUS_OPEN: {"label": "Открыт", "color": "neutral"},
    SupportThread.STATUS_WAITING_USER: {"label": "Ждет пользователя", "color": "warn"},
    SupportThread.STATUS_WAITING_SUPPORT: {"label": "Ждет поддержки", "color": "info"},
    SupportThread.STATUS_CLOSED: {"label": "Закрыт", "color": "muted"},
}


def _is_support_user(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


def _support_users_qs():
    return User.objects.filter(is_superuser=True, is_active=True)


def _thread_status_payload(status: str) -> dict:
    meta = SUPPORT_STATUS_META.get(status, SUPPORT_STATUS_META[SupportThread.STATUS_OPEN])
    return {"code": status, "label": meta["label"], "color": meta["color"]}


def _touch_thread_status_on_message(thread: SupportThread, author_role: str) -> None:
    if thread.status == SupportThread.STATUS_CLOSED:
        thread.status = SupportThread.STATUS_WAITING_USER if author_role == SupportMessage.ROLE_SUPPORT else SupportThread.STATUS_WAITING_SUPPORT
        thread.closed_at = None
    else:
        thread.status = SupportThread.STATUS_WAITING_USER if author_role == SupportMessage.ROLE_SUPPORT else SupportThread.STATUS_WAITING_SUPPORT
    thread.save(update_fields=["status", "closed_at", "updated_at"])


def _increment_thread_unread(thread: SupportThread, from_role: str) -> None:
    if from_role == SupportMessage.ROLE_SUPPORT:
        targets = [thread.user]
    else:
        targets = list(_support_users_qs())
    now_dt = timezone.now()
    for target in targets:
        state, _ = SupportThreadParticipantState.objects.get_or_create(
            thread=thread,
            user=target,
            defaults={"unread_count": 0, "last_read_at": now_dt},
        )
        state.unread_count = int(state.unread_count or 0) + 1
        state.save(update_fields=["unread_count"])


def _mark_thread_read(thread: SupportThread, user) -> None:
    now_dt = timezone.now()
    state, _ = SupportThreadParticipantState.objects.get_or_create(
        thread=thread,
        user=user,
        defaults={"unread_count": 0, "last_read_at": now_dt},
    )
    state.unread_count = 0
    state.last_read_at = now_dt
    state.save(update_fields=["unread_count", "last_read_at"])


def _can_access_thread(user, thread: SupportThread) -> bool:
    if _is_support_user(user):
        return True
    return thread.user_id == user.id


def _serialize_thread_for_list(thread: SupportThread, unread_count: int, last_message: SupportMessage | None) -> dict:
    last_body = (last_message.body if last_message else "").strip()
    preview = (last_body[:120] + "...") if len(last_body) > 120 else last_body
    last_author_role = last_message.author_role if last_message else ""
    return {
        "id": thread.id,
        "subject": thread.subject,
        "status": _thread_status_payload(thread.status),
        "owner": {
            "id": thread.user_id,
            "username": thread.user.username,
        },
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "unread_count": int(unread_count or 0),
        "last_message_preview": preview,
        "last_message_author_role": last_author_role,
    }


@login_required
def support_chat(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    return render(
        request,
        "support/chat.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
        },
    )


@login_required
def support_chat_admin(request):
    if not _is_support_user(request.user):
        return redirect("support_chat")
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    return render(
        request,
        "support/chat_admin.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
        },
    )


@login_required
def support_threads_api(request):
    if request.method == "GET":
        is_support = _is_support_user(request.user)
        qs = SupportThread.objects.select_related("user").order_by("-updated_at")
        if not is_support:
            qs = qs.filter(user=request.user)
        else:
            status_filter = (request.GET.get("status") or "").strip()
            query = (request.GET.get("q") or "").strip()
            if status_filter in {code for code, _label in SupportThread.STATUS_CHOICES}:
                qs = qs.filter(status=status_filter)
            if query:
                qs = qs.filter(Q(subject__icontains=query) | Q(user__username__icontains=query))

        thread_ids = [row.id for row in qs[:200]]
        unread_map = {
            row["thread_id"]: int(row.get("unread_count") or 0)
            for row in (
                SupportThreadParticipantState.objects
                .filter(user=request.user, thread_id__in=thread_ids)
                .values("thread_id", "unread_count")
            )
        }
        last_messages = list(
            SupportMessage.objects
            .filter(thread_id__in=thread_ids)
            .select_related("author_user")
            .order_by("thread_id", "-created_at")
        )
        last_message_map: dict[int, SupportMessage] = {}
        for msg in last_messages:
            if msg.thread_id not in last_message_map and (is_support or not msg.is_internal):
                last_message_map[msg.thread_id] = msg

        payload = [
            _serialize_thread_for_list(
                thread,
                unread_map.get(thread.id, 0),
                last_message_map.get(thread.id),
            )
            for thread in qs[:200]
        ]
        return JsonResponse({"ok": True, "threads": payload}, status=200)

    if request.method == "POST":
        subject = (request.POST.get("subject") or "").strip()
        body = (request.POST.get("body") or "").strip()
        if len(subject) < 3:
            return JsonResponse({"error": "Тема слишком короткая (минимум 3 символа)."}, status=400)
        if len(body) < 3:
            return JsonResponse({"error": "Сообщение слишком короткое (минимум 3 символа)."}, status=400)

        thread = SupportThread.objects.create(
            user=request.user,
            subject=subject[:255],
            status=SupportThread.STATUS_WAITING_SUPPORT,
        )
        SupportMessage.objects.create(
            thread=thread,
            author_user=request.user,
            author_role=SupportMessage.ROLE_USER,
            body=body,
        )
        _increment_thread_unread(thread, from_role=SupportMessage.ROLE_USER)
        _mark_thread_read(thread, request.user)
        return JsonResponse({"ok": True, "thread_id": thread.id}, status=201)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
def support_thread_messages_api(request, thread_id: int):
    try:
        thread = SupportThread.objects.select_related("user").get(id=thread_id)
    except SupportThread.DoesNotExist:
        return JsonResponse({"error": "Диалог не найден."}, status=404)

    if not _can_access_thread(request.user, thread):
        return JsonResponse({"error": "forbidden"}, status=403)

    is_support = _is_support_user(request.user)
    if request.method == "GET":
        messages_qs = (
            SupportMessage.objects
            .filter(thread=thread)
            .select_related("author_user")
            .order_by("created_at", "id")
        )
        if not is_support:
            messages_qs = messages_qs.filter(is_internal=False)
        messages_payload = [
            {
                "id": msg.id,
                "thread_id": msg.thread_id,
                "body": msg.body,
                "author_role": msg.author_role,
                "author_username": msg.author_user.username,
                "is_mine": msg.author_user_id == request.user.id,
                "is_internal": bool(msg.is_internal),
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg in messages_qs
        ]
        return JsonResponse(
            {
                "ok": True,
                "thread": {
                    "id": thread.id,
                    "subject": thread.subject,
                    "status": _thread_status_payload(thread.status),
                    "owner": {"id": thread.user_id, "username": thread.user.username},
                    "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
                },
                "messages": messages_payload,
            },
            status=200,
        )

    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        if len(body) < 1:
            return JsonResponse({"error": "Пустое сообщение."}, status=400)
        is_internal = (request.POST.get("is_internal") or "0").strip() in {"1", "true", "on"}
        if is_internal and not is_support:
            return JsonResponse({"error": "forbidden"}, status=403)
        role = SupportMessage.ROLE_SUPPORT if is_support else SupportMessage.ROLE_USER
        msg = SupportMessage.objects.create(
            thread=thread,
            author_user=request.user,
            author_role=role,
            body=body,
            is_internal=is_internal,
        )
        _touch_thread_status_on_message(thread, role)
        if not is_internal:
            _increment_thread_unread(thread, from_role=role)
        _mark_thread_read(thread, request.user)
        return JsonResponse(
            {
                "ok": True,
                "message": {
                    "id": msg.id,
                    "thread_id": msg.thread_id,
                    "body": msg.body,
                    "author_role": msg.author_role,
                    "author_username": msg.author_user.username,
                    "is_mine": True,
                    "is_internal": bool(msg.is_internal),
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                },
                "thread_status": _thread_status_payload(thread.status),
            },
            status=201,
        )

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@require_POST
def support_thread_read_api(request, thread_id: int):
    try:
        thread = SupportThread.objects.get(id=thread_id)
    except SupportThread.DoesNotExist:
        return JsonResponse({"error": "Диалог не найден."}, status=404)
    if not _can_access_thread(request.user, thread):
        return JsonResponse({"error": "forbidden"}, status=403)
    _mark_thread_read(thread, request.user)
    return JsonResponse({"ok": True}, status=200)


@login_required
@require_POST
def support_thread_status_api(request, thread_id: int):
    if not _is_support_user(request.user):
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        thread = SupportThread.objects.get(id=thread_id)
    except SupportThread.DoesNotExist:
        return JsonResponse({"error": "Диалог не найден."}, status=404)
    new_status = (request.POST.get("status") or "").strip()
    valid_statuses = {code for code, _label in SupportThread.STATUS_CHOICES}
    if new_status not in valid_statuses:
        return JsonResponse({"error": "Некорректный статус."}, status=400)
    thread.status = new_status
    thread.closed_at = timezone.now() if new_status == SupportThread.STATUS_CLOSED else None
    thread.save(update_fields=["status", "closed_at", "updated_at"])
    return JsonResponse({"ok": True, "status": _thread_status_payload(thread.status)}, status=200)


@login_required
@require_GET
def support_unread_count_api(request):
    unread = (
        SupportThreadParticipantState.objects
        .filter(user=request.user)
        .aggregate(total=Coalesce(Sum("unread_count"), Value(0)))
        .get("total")
    )
    return JsonResponse({"ok": True, "unread_count": int(unread or 0)}, status=200)
