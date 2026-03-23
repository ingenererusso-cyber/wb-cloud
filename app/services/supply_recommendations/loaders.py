from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Any

from django.db.models import Count, Q

from core.models import (
    Order,
    Product,
    SellerAccount,
    TransitDirectionTariff,
    WbAcceptanceCoefficient,
    WbWarehouseTariff,
)
from core.services.localization import find_office, normalize_district
from core.services_realization import _calculate_box_logistics_per_unit

from .constants import UNKNOWN_REGION
from .models import OrderAggregate, TransitTariff, WarehouseCoefficient

# Если для части артикулов объем еще не синхронизирован из WB карточек,
# используем безопасный дефолт, чтобы расчеты работали стабильно.
DEFAULT_ARTICLE_VOLUME_LITERS = 4.0


def _normalize_str(value: str | None, fallback: str = "") -> str:
    if not value:
        return fallback
    return value.strip() or fallback


def _normalize_region(value: str | None) -> str:
    raw = _normalize_str(value)
    if not raw:
        return UNKNOWN_REGION
    normalized = normalize_district(raw)
    return _normalize_str(normalized, fallback=UNKNOWN_REGION)


def _is_regular_warehouse_name(name: str) -> bool:
    normalized = _normalize_str(name).lower().replace("ё", "е")
    if not normalized:
        return False
    if "питание" in normalized:
        return False
    if "сгт" in normalized:
        return False
    if normalized.startswith("сц") or " сц " in f" {normalized} ":
        return False
    return True


def _load_latest_tariffs_by_warehouse(
    warehouse_names: Iterable[str],
    seller: SellerAccount | None = None,
    tariff_date: date | None = None,
) -> Dict[str, WbWarehouseTariff]:
    normalized_names = [_normalize_str(name) for name in warehouse_names if _normalize_str(name)]
    if not normalized_names:
        return {}

    latest_by_warehouse: Dict[str, WbWarehouseTariff] = {}

    def _fill_from_queryset(qs):
        if tariff_date is not None:
            local_qs = qs.filter(tariff_date=tariff_date)
        else:
            local_qs = qs.order_by("-tariff_date")
        for row in local_qs.iterator(chunk_size=1000):
            wh_name = _normalize_str(row.warehouse_name)
            if not wh_name:
                continue
            if wh_name not in latest_by_warehouse:
                latest_by_warehouse[wh_name] = row

    base_qs = WbWarehouseTariff.objects.filter(warehouse_name__in=normalized_names)
    if seller is not None:
        _fill_from_queryset(base_qs.filter(seller=seller))
        missing = [name for name in normalized_names if name not in latest_by_warehouse]
        if missing:
            _fill_from_queryset(base_qs.filter(warehouse_name__in=missing).exclude(seller=seller))
    else:
        _fill_from_queryset(base_qs)

    return latest_by_warehouse


def _pick_nearest_acceptance_coef_row(
    rows: List[WbAcceptanceCoefficient],
    target_date: date,
) -> WbAcceptanceCoefficient | None:
    """
    Возвращает строку коэффициента приёмки:
    - сначала на target_date (обычно сегодня),
    - иначе ближайшую по дате.
    """
    if not rows:
        return None

    exact = [row for row in rows if row.coeff_date == target_date]
    if exact:
        return exact[0]

    # При равной дистанции предпочитаем более свежую дату.
    return min(rows, key=lambda row: (abs((row.coeff_date - target_date).days), -row.coeff_date.toordinal()))


def _load_acceptance_delivery_coef_by_warehouse(
    warehouse_names: Iterable[str],
    seller: SellerAccount | None = None,
    target_date: date | None = None,
    box_type_id: int = 2,
) -> Dict[str, float]:
    """
    Загружает коэффициенты логистики складов из WbAcceptanceCoefficient.

    Логика даты:
    - берём запись на target_date (сегодня), если есть;
    - иначе ближайшую доступную дату по складу.

    Возвращаем logistics_coef в виде множителя (delivery_coef / 100).
    """
    normalized_names = [_normalize_str(name) for name in warehouse_names if _normalize_str(name)]
    if not normalized_names:
        return {}

    rows_by_warehouse: Dict[str, List[WbAcceptanceCoefficient]] = {}

    def _append_rows(qs):
        for row in qs.iterator(chunk_size=2000):
            warehouse_name = _normalize_str(row.warehouse_name)
            if not warehouse_name:
                continue
            rows_by_warehouse.setdefault(warehouse_name, []).append(row)

    base_qs = (
        WbAcceptanceCoefficient.objects
        .filter(warehouse_name__in=normalized_names, box_type_id=box_type_id)
        .exclude(delivery_coef__isnull=True)
    )
    if seller is not None:
        _append_rows(base_qs.filter(seller=seller))
        missing = [name for name in normalized_names if name not in rows_by_warehouse]
        if missing:
            _append_rows(base_qs.filter(warehouse_name__in=missing).exclude(seller=seller))
    else:
        _append_rows(base_qs)

    if not rows_by_warehouse:
        return {}

    as_of = target_date or date.today()
    result: Dict[str, float] = {}
    for warehouse_name, rows in rows_by_warehouse.items():
        picked = _pick_nearest_acceptance_coef_row(rows, target_date=as_of)
        if not picked or picked.delivery_coef is None:
            continue
        delivery_coef = float(picked.delivery_coef)
        if delivery_coef <= 0:
            continue
        result[warehouse_name] = round(delivery_coef / 100.0, 6)
    return result


def _load_volume_by_supplier_article(seller: SellerAccount | None = None) -> Dict[str, float]:
    """
    Загружает карту объема по supplier_article.
    """
    products_qs = Product.objects
    if seller is not None:
        products_qs = products_qs.filter(seller=seller)

    rows = (
        products_qs
        .exclude(vendor_code__isnull=True)
        .exclude(vendor_code="")
        .exclude(volume_liters__isnull=True)
        .values("vendor_code", "volume_liters")
    )

    volume_map: Dict[str, float] = {}
    for row in rows:
        supplier_article = _normalize_str(row["vendor_code"])
        if not supplier_article:
            continue
        # Если есть дубли по seller/nm_id, берём максимальный объём как более безопасный.
        volume_map[supplier_article] = max(
            float(row["volume_liters"] or 0.0),
            volume_map.get(supplier_article, 0.0),
        )

    return volume_map


def load_order_aggregates(
    date_from: date,
    date_to: date,
    seller: SellerAccount | None = None,
) -> List[OrderAggregate]:
    """
    Загружает и агрегирует заказы по supplier_article / региону заказа / складу отгрузки.

    Адаптация к текущей модели Order:
    - order_region -> oblast_okrug_name
    - shipment_warehouse -> warehouse_name

    Возвращает list[OrderAggregate] с количеством заказов и средним объемом.
    """
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")

    volume_map = _load_volume_by_supplier_article(seller=seller)

    orders_qs = Order.objects.filter(
        order_date__date__gte=date_from,
        order_date__date__lte=date_to,
        warehouse_type="Склад WB",
    )
    if seller is not None:
        orders_qs = orders_qs.filter(seller=seller)

    grouped = (
        orders_qs
        .values("nm_id", "supplier_article", "oblast_okrug_name", "warehouse_name")
        .annotate(
            orders_count=Count("id"),
            local_orders_count=Count("id", filter=Q(is_local=True)),
        )
        .order_by("supplier_article", "oblast_okrug_name", "warehouse_name")
    )

    result: List[OrderAggregate] = []
    for row in grouped:
        supplier_article = _normalize_str(row["supplier_article"])
        result.append(
            OrderAggregate(
                nm_id=row["nm_id"],
                supplier_article=supplier_article,
                order_region=_normalize_region(row["oblast_okrug_name"]),
                shipment_warehouse=_normalize_str(row["warehouse_name"], fallback="Не указан"),
                orders_count=int(row["orders_count"] or 0),
                avg_volume_liters=float(volume_map.get(supplier_article, DEFAULT_ARTICLE_VOLUME_LITERS)),
                local_orders_count=int(row["local_orders_count"] or 0),
            )
        )

    return result


def _pick_tariff_for_date(
    tariffs_by_warehouse: Dict[str, List[WbWarehouseTariff]],
    warehouse_name: str,
    target_date: date,
) -> WbWarehouseTariff | None:
    tariffs = tariffs_by_warehouse.get(_normalize_str(warehouse_name), [])
    if not tariffs:
        return None
    for tariff in tariffs:
        if tariff.tariff_date <= target_date:
            return tariff
    return tariffs[0]


def calculate_theoretical_logistics_sum_for_period(
    date_from: date,
    date_to: date,
    seller: SellerAccount | None = None,
) -> float:
    """
    Суммирует теоретическую логистику по каждому FBW заказу за период.

    Включает отмененные заказы (по требованию бизнес-логики).
    Формула для заказа:
        theoretical_order_cost = base_by_volume * delivery_coef
    где:
        base_by_volume определяется по модели МГТ (_calculate_box_logistics_per_unit),
        delivery_coef = boxDeliveryCoefExpr / 100 (или 1.0, если не найден).
    """
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")

    orders_qs = Order.objects.filter(
        order_date__date__gte=date_from,
        order_date__date__lte=date_to,
        warehouse_type="Склад WB",
    )
    if seller is not None:
        orders_qs = orders_qs.filter(seller=seller)

    orders = list(
        orders_qs.values("supplier_article", "warehouse_name", "order_date")
    )
    if not orders:
        return 0.0

    volume_map = _load_volume_by_supplier_article(seller=seller)
    warehouse_names = {_normalize_str(item["warehouse_name"]) for item in orders if _normalize_str(item["warehouse_name"])}
    tariffs_by_warehouse: Dict[str, List[WbWarehouseTariff]] = {}

    def _append_tariffs(qs):
        for tariff in qs.iterator(chunk_size=1000):
            warehouse_name = _normalize_str(tariff.warehouse_name)
            if not warehouse_name:
                continue
            tariffs_by_warehouse.setdefault(warehouse_name, []).append(tariff)

    base_qs = (
        WbWarehouseTariff.objects
        .filter(warehouse_name__in=list(warehouse_names))
        .order_by("warehouse_name", "-tariff_date")
    )
    if seller is not None:
        _append_tariffs(base_qs.filter(seller=seller))
        missing_warehouses = [name for name in warehouse_names if name not in tariffs_by_warehouse]
        if missing_warehouses:
            _append_tariffs(base_qs.filter(warehouse_name__in=missing_warehouses).exclude(seller=seller))
    else:
        _append_tariffs(base_qs)

    total = 0.0
    for item in orders:
        supplier_article = _normalize_str(item["supplier_article"])
        warehouse_name = _normalize_str(item["warehouse_name"])
        order_dt = item["order_date"]
        if not warehouse_name or not order_dt:
            continue

        order_date_only = order_dt.date()
        tariff = _pick_tariff_for_date(tariffs_by_warehouse, warehouse_name, order_date_only)
        coef_expr = float(tariff.box_delivery_coef_expr or 0.0) if tariff else 0.0
        delivery_coef = (coef_expr / 100.0) if coef_expr > 0 else 1.0

        volume_liters = float(volume_map.get(supplier_article, DEFAULT_ARTICLE_VOLUME_LITERS))
        base_by_volume = _calculate_box_logistics_per_unit(0.0, 0.0, volume_liters)
        total += base_by_volume * delivery_coef

    return round(total, 2)


def build_region_order_summary(order_aggregates: List[OrderAggregate]) -> Dict[str, int]:
    """Суммирует количество заказов по регионам."""
    summary: Dict[str, int] = {}
    for item in order_aggregates:
        summary[item.order_region] = summary.get(item.order_region, 0) + item.orders_count
    return summary


def build_article_region_summary(order_aggregates: List[OrderAggregate]) -> Dict[tuple[str, str], int]:
    """Суммирует количество заказов по ключу (supplier_article, region_name)."""
    summary: Dict[tuple[str, str], int] = {}
    for item in order_aggregates:
        key = (item.supplier_article, item.order_region)
        summary[key] = summary.get(key, 0) + item.orders_count
    return summary


def build_default_warehouse_coefficients(order_aggregates: Iterable[OrderAggregate]) -> List[WarehouseCoefficient]:
    """
    Build fallback warehouse coefficients from order aggregates.

    Produces one coefficient per (shipment_warehouse, order_region) pair with neutral
    logistics coefficient 1.0. This keeps pipeline operational when no external
    coefficients source is configured yet.
    """
    seen: set[tuple[str, str]] = set()
    result: List[WarehouseCoefficient] = []
    for item in order_aggregates:
        warehouse_name = _normalize_str(item.shipment_warehouse)
        region_name = _normalize_region(item.order_region)
        if not warehouse_name or not region_name:
            continue
        key = (warehouse_name, region_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            WarehouseCoefficient(
                warehouse_name=warehouse_name,
                region_name=region_name,
                logistics_coef=1.0,
                storage_coef=None,
            )
        )
    return result


def load_warehouse_coefficients_from_tariffs(
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
    tariff_date: date | None = None,
    extra_warehouses: Iterable[str] | None = None,
) -> List[WarehouseCoefficient]:
    """
    Строит коэффициенты складов для расчёта рекомендаций.

    Источник: WbAcceptanceCoefficient.delivery_coef (тип поставки: короба),
    как на странице "Тарифы на поставку: коэффициенты".

    Дата:
    - сегодня, если есть запись;
    - иначе ближайшая доступная дата по складу.

    Значение: logistics_coef = delivery_coef / 100.
    """
    region_by_warehouse: Dict[str, str] = {}
    for item in order_aggregates:
        warehouse_name = _normalize_str(item.shipment_warehouse)
        region_name = _normalize_region(item.order_region)
        if warehouse_name and region_name and warehouse_name not in region_by_warehouse:
            region_by_warehouse[warehouse_name] = region_name
    for warehouse_name in (extra_warehouses or []):
        normalized_name = _normalize_str(warehouse_name)
        if normalized_name and normalized_name not in region_by_warehouse:
            region_by_warehouse[normalized_name] = UNKNOWN_REGION

    if not region_by_warehouse:
        return []

    acceptance_coef_by_warehouse = _load_acceptance_delivery_coef_by_warehouse(
        warehouse_names=region_by_warehouse.keys(),
        seller=seller,
        target_date=tariff_date or date.today(),
        box_type_id=2,
    )

    result: List[WarehouseCoefficient] = []
    for warehouse_name, region_name in region_by_warehouse.items():
        acceptance_coef = acceptance_coef_by_warehouse.get(warehouse_name)
        if acceptance_coef is not None and acceptance_coef > 0:
            logistics_coef = float(acceptance_coef)
        else:
            logistics_coef = 1.0
        result.append(
            WarehouseCoefficient(
                warehouse_name=warehouse_name,
                region_name=region_name,
                logistics_coef=round(logistics_coef, 6),
                storage_coef=None,
            )
        )

    return result


def list_regular_warehouses(seller: SellerAccount | None = None) -> List[str]:
    base_qs = (
        WbWarehouseTariff.objects
        .exclude(warehouse_name__isnull=True)
        .exclude(warehouse_name="")
    )
    if seller is not None:
        seller_names = list(
            base_qs.filter(seller=seller)
            .order_by("warehouse_name")
            .values_list("warehouse_name", flat=True)
            .distinct()
        )
        regular_seller_names = [name for name in seller_names if _is_regular_warehouse_name(name)]
        if regular_seller_names:
            return regular_seller_names

        fallback_names = list(
            base_qs.exclude(seller=seller)
            .order_by("warehouse_name")
            .values_list("warehouse_name", flat=True)
            .distinct()
        )
        return [name for name in fallback_names if _is_regular_warehouse_name(name)]

    names = list(
        base_qs.order_by("warehouse_name")
        .values_list("warehouse_name", flat=True)
        .distinct()
    )
    return [name for name in names if _is_regular_warehouse_name(name)]


def get_warehouse_logistics_coef(
    warehouse_name: str,
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
    tariff_date: date | None = None,
) -> float | None:
    normalized_name = _normalize_str(warehouse_name)
    if not normalized_name:
        return None
    coefficients = load_warehouse_coefficients_from_tariffs(
        order_aggregates=order_aggregates,
        seller=seller,
        tariff_date=tariff_date,
        extra_warehouses=[normalized_name],
    )
    for item in coefficients:
        if _normalize_str(item.warehouse_name) == normalized_name:
            return float(item.logistics_coef)
    return None


def estimate_base_logistics_per_order_from_tariffs(
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
    tariff_date: date | None = None,
    fallback_value: float = 50.0,
) -> float:
    """
    Оценивает среднюю фактическую логистику на заказ по текущим отгрузкам.

    cost_per_order = box_delivery_base + box_delivery_liter * avg_volume_liters
    Итоговое значение — средневзвешенное по количеству заказов.
    """
    aggregates = [item for item in order_aggregates if item.orders_count > 0]
    if not aggregates:
        return float(fallback_value)

    latest_by_warehouse = _load_latest_tariffs_by_warehouse(
        warehouse_names={item.shipment_warehouse for item in aggregates},
        seller=seller,
        tariff_date=tariff_date,
    )

    total_cost = 0.0
    total_orders = 0
    for item in aggregates:
        tariff = latest_by_warehouse.get(_normalize_str(item.shipment_warehouse))
        if not tariff:
            continue

        base_part = float(tariff.box_delivery_base or 0.0)
        liter_part = float(tariff.box_delivery_liter or 0.0) * max(float(item.avg_volume_liters), 0.0)
        cost_per_order = base_part + liter_part
        if cost_per_order <= 0:
            continue

        total_cost += cost_per_order * item.orders_count
        total_orders += item.orders_count

    if total_orders <= 0:
        return float(fallback_value)
    return round(total_cost / total_orders, 6)


def load_transit_tariffs_from_tariffs(
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
    tariff_date: date | None = None,
) -> List[TransitTariff]:
    """
    Строит тарифы транзита по регионам из WbWarehouseTariff (boxDeliveryLiter).
    """
    region_by_warehouse: Dict[str, str] = {}
    for item in order_aggregates:
        warehouse_name = _normalize_str(item.shipment_warehouse)
        region_name = _normalize_region(item.order_region)
        if warehouse_name and region_name and warehouse_name not in region_by_warehouse:
            region_by_warehouse[warehouse_name] = region_name

    if not region_by_warehouse:
        return []

    qs = WbWarehouseTariff.objects.filter(warehouse_name__in=list(region_by_warehouse.keys()))
    if seller is not None:
        qs = qs.filter(seller=seller)
    if tariff_date is not None:
        qs = qs.filter(tariff_date=tariff_date)
    else:
        qs = qs.order_by("-tariff_date")

    latest_by_warehouse: Dict[str, WbWarehouseTariff] = {}
    for row in qs.iterator(chunk_size=1000):
        if row.warehouse_name not in latest_by_warehouse:
            latest_by_warehouse[row.warehouse_name] = row

    region_prices: Dict[str, List[float]] = {}
    for warehouse_name, region_name in region_by_warehouse.items():
        tariff = latest_by_warehouse.get(warehouse_name)
        if not tariff or tariff.box_delivery_liter is None:
            continue
        region_prices.setdefault(region_name, []).append(float(tariff.box_delivery_liter))

    result: List[TransitTariff] = []
    for region_name, prices in region_prices.items():
        if not prices:
            continue
        result.append(
            TransitTariff(
                target_region=region_name,
                price_per_liter=round(min(prices), 6),
            )
        )
    return result


def load_transit_tariffs_from_directions(
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
) -> List[TransitTariff]:
    """
    Строит тарифы транзита по регионам из TransitDirectionTariff.

    Для каждого региона берется минимальная доступная ставка за литр:
    сначала < 1500 л, если её нет — > 1500 л.
    """
    needed_regions = {
        _normalize_region(item.order_region)
        for item in order_aggregates
        if item.orders_count > 0
    }
    if not needed_regions:
        return []

    base_qs = TransitDirectionTariff.objects.all()
    row_sources: List[Any] = []
    if seller is not None:
        seller_rows = list(base_qs.filter(seller=seller).iterator(chunk_size=1000))
        row_sources.extend(seller_rows)
        if not seller_rows:
            row_sources.extend(base_qs.exclude(seller=seller).iterator(chunk_size=1000))
    else:
        row_sources.extend(base_qs.iterator(chunk_size=1000))

    region_prices: Dict[str, List[float]] = {}
    for row in row_sources:
        region_name = _resolve_target_region(row)
        if not region_name or region_name not in needed_regions:
            continue

        per_liter = row.box_price_per_liter_lt_1500
        if per_liter is None:
            per_liter = row.box_price_per_liter_gt_1500
        if per_liter is None:
            continue

        region_prices.setdefault(region_name, []).append(float(per_liter))

    return [
        TransitTariff(target_region=region_name, price_per_liter=round(min(prices), 6), target_warehouse_name=None)
        for region_name, prices in sorted(region_prices.items())
        if prices
    ]


def list_available_transit_warehouses(seller: SellerAccount | None = None) -> List[str]:
    base_qs = (
        TransitDirectionTariff.objects
        .exclude(transit_warehouse__isnull=True)
        .exclude(transit_warehouse="")
    )
    if seller is not None:
        seller_names = list(
            base_qs.filter(seller=seller)
            .order_by("transit_warehouse")
            .values_list("transit_warehouse", flat=True)
            .distinct()
        )
        if seller_names:
            return seller_names
        return list(
            base_qs.exclude(seller=seller)
            .order_by("transit_warehouse")
            .values_list("transit_warehouse", flat=True)
            .distinct()
        )
    return list(
        base_qs.order_by("transit_warehouse")
        .values_list("transit_warehouse", flat=True)
        .distinct()
    )


def _resolve_target_region(row: Any) -> str | None:
    target_warehouse = _normalize_str(row.target_warehouse)
    if "шушар" in target_warehouse.lower().replace("ё", "е"):
        return "Северо-Западный федеральный округ"

    target_region = _normalize_str(row.target_region)
    if target_region:
        return normalize_district(target_region) or target_region

    if not target_warehouse:
        return None

    office = find_office(target_warehouse)
    if office and office.federal_district:
        normalized = normalize_district(office.federal_district)
        return normalized or office.federal_district
    return None


def load_transit_tariffs_for_transit_warehouse(
    transit_warehouse: str,
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
) -> List[TransitTariff]:
    """
    Загружает тарифы по выбранному транзитному складу.

    Использует таблицу TransitDirectionTariff. Для расчета берется ставка для
    коробов < 1500 л, а если она отсутствует — для > 1500 л.
    """
    transit_warehouse = _normalize_str(transit_warehouse)
    if not transit_warehouse:
        return []

    needed_regions = {
        _normalize_region(item.order_region)
        for item in order_aggregates
        if item.orders_count > 0
    }
    if not needed_regions:
        return []

    options_by_region = load_transit_tariff_options_for_transit_warehouse(
        transit_warehouse=transit_warehouse,
        order_aggregates=order_aggregates,
        seller=seller,
    )
    return [items[0] for _, items in sorted(options_by_region.items()) if items]


def load_transit_tariff_options_for_transit_warehouse(
    transit_warehouse: str,
    order_aggregates: Iterable[OrderAggregate],
    seller: SellerAccount | None = None,
) -> Dict[str, List[TransitTariff]]:
    """
    Возвращает все доступные склады назначения по выбранному транзитному складу.

    Группировка: region -> list[TransitTariff], где каждый элемент соответствует
    конкретному складу назначения (лучшей ставке для этого склада).
    """
    transit_warehouse = _normalize_str(transit_warehouse)
    if not transit_warehouse:
        return {}

    needed_regions = {
        _normalize_region(item.order_region)
        for item in order_aggregates
        if item.orders_count > 0
    }
    if not needed_regions:
        return {}

    base_qs = TransitDirectionTariff.objects.filter(transit_warehouse=transit_warehouse)
    row_sources: List[Any] = []
    if seller is not None:
        seller_rows = list(base_qs.filter(seller=seller).iterator(chunk_size=1000))
        row_sources.extend(seller_rows)
        if not seller_rows:
            row_sources.extend(base_qs.exclude(seller=seller).iterator(chunk_size=1000))
    else:
        row_sources.extend(base_qs.iterator(chunk_size=1000))

    # Выбираем минимальную ставку для каждой пары (регион, склад назначения),
    # чтобы убрать дубли по обновлениям в исходной таблице.
    best_by_region_warehouse: Dict[tuple[str, str | None], float] = {}
    for row in row_sources:
        region_name = _resolve_target_region(row)
        if not region_name or region_name not in needed_regions:
            continue

        per_liter = row.box_price_per_liter_lt_1500
        if per_liter is None:
            per_liter = row.box_price_per_liter_gt_1500
        if per_liter is None:
            continue

        destination_warehouse = _normalize_str(row.target_warehouse) or None
        key = (region_name, destination_warehouse)
        price = float(per_liter)
        prev = best_by_region_warehouse.get(key)
        if prev is None or price < prev:
            best_by_region_warehouse[key] = price

    result: Dict[str, List[TransitTariff]] = {}
    for (region_name, destination_warehouse), price in best_by_region_warehouse.items():
        result.setdefault(region_name, []).append(
            TransitTariff(
                target_region=region_name,
                price_per_liter=round(price, 6),
                target_warehouse_name=destination_warehouse,
            )
        )

    for region_name, items in result.items():
        items.sort(key=lambda item: (item.price_per_liter, item.target_warehouse_name or ""))

    return result
