from __future__ import annotations

from datetime import date
from datetime import timedelta
from typing import Any

from core.models import (
    SellerAccount,
    TransitDirectionTariff,
    WbAcceptanceCoefficient,
    WbWarehouseTariff,
)
from core.services.localization import find_office, normalize_district
from wb_api.client import WBCommonClient, WBSuppliesClient


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().replace(" ", "").replace(",", ".")
        if not normalized or normalized in {"-", "—", "нет", "None", "null"}:
            return None
        value = normalized
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_volume_tariffs(box_tariff: Any) -> tuple[float | None, float | None]:
    """
    Возвращает тарифы за литр для коробов:
    - первый элемент: < 1500 л
    - второй элемент: > 1500 л

    WB может вернуть `null`, пустой список или список объектов с
    разной схемой полей. Поэтому используем устойчивый best-effort парсинг.
    """
    if not isinstance(box_tariff, list) or not box_tariff:
        return (None, None)

    parsed_values: list[float] = []
    lt_1500: float | None = None
    gt_1500: float | None = None

    for item in box_tariff:
        if not isinstance(item, dict):
            continue

        value = _to_float(
            item.get("tariff")
            or item.get("price")
            or item.get("value")
            or item.get("pricePerLiter")
            or item.get("tariffPerLiter")
        )
        if value is None:
            continue

        parsed_values.append(value)

        lower = _to_float(item.get("lowerBound"))
        upper = _to_float(item.get("upperBound"))
        if upper is not None and upper <= 1500:
            lt_1500 = value
            continue
        if lower is not None and lower >= 1500:
            gt_1500 = value
            continue

    if lt_1500 is None and parsed_values:
        lt_1500 = parsed_values[0]
    if gt_1500 is None and len(parsed_values) > 1:
        gt_1500 = parsed_values[-1]

    return (lt_1500, gt_1500)


def _resolve_target_region(target_warehouse: str) -> str | None:
    office = find_office(target_warehouse)
    if not office or not office.federal_district:
        return None
    return normalize_district(office.federal_district) or office.federal_district


def _normalize_target_region_for_known_warehouses(target_warehouse: str, target_region: str | None) -> str | None:
    """
    Исправляет известные аномалии определения региона склада назначения.

    Шушары относятся к СЗФО. Если по ошибке получился "Восток" — заменяем.
    """
    normalized_warehouse = _normalize_text(target_warehouse).lower().replace("ё", "е")
    if "шушар" in normalized_warehouse and _normalize_text(target_region) == "Восток":
        return "Северо-Западный федеральный округ"
    return target_region


def sync_warehouse_tariffs(seller: SellerAccount, on_date: date | None = None) -> int:
    """
    Синхронизирует тарифы WB common-api /tariffs/box по складам.
    """
    client = WBCommonClient(seller.api_token)
    tariff_date = on_date or date.today()
    warehouse_list = client.get_tariffs_box(on_date=tariff_date)

    synced = 0
    for row in warehouse_list:
        warehouse_name = (row.get("warehouseName") or "").strip()
        if not warehouse_name:
            continue

        WbWarehouseTariff.objects.update_or_create(
            seller=seller,
            warehouse_name=warehouse_name,
            tariff_date=tariff_date,
            defaults={
                "geo_name": _normalize_text(row.get("geoName")) or None,
                "box_delivery_base": _to_float(row.get("boxDeliveryBase")),
                "box_delivery_coef_expr": _to_float(row.get("boxDeliveryCoefExpr")),
                "box_delivery_liter": _to_float(row.get("boxDeliveryLiter")),
                "box_delivery_marketplace_base": _to_float(row.get("boxDeliveryMarketplaceBase")),
                "box_delivery_marketplace_coef_expr": _to_float(row.get("boxDeliveryMarketplaceCoefExpr")),
                "box_delivery_marketplace_liter": _to_float(row.get("boxDeliveryMarketplaceLiter")),
                "box_storage_base": _to_float(row.get("boxStorageBase")),
                "box_storage_coef_expr": _to_float(row.get("boxStorageCoefExpr")),
                "box_storage_liter": _to_float(row.get("boxStorageLiter")),
            },
        )
        synced += 1

    return synced


def sync_warehouse_tariffs_for_period(
    seller: SellerAccount,
    date_from: date,
    date_to: date,
) -> int:
    """
    Синхронизирует исторические тарифы коробов за период [date_from, date_to].
    """
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")

    total_synced = 0
    current = date_from
    while current <= date_to:
        total_synced += sync_warehouse_tariffs(seller=seller, on_date=current)
        current += timedelta(days=1)
    return total_synced


def sync_transit_direction_tariffs(seller: SellerAccount) -> int:
    """
    Синхронизирует транзитные направления WB Supplies API /transit-tariffs.
    """
    client = WBSuppliesClient(seller.api_token)
    rows = client.get_transit_tariffs()

    synced = 0
    for row in rows:
        transit_warehouse = _normalize_text(row.get("transitWarehouseName"))
        target_warehouse = _normalize_text(row.get("destinationWarehouseName"))
        if not transit_warehouse or not target_warehouse:
            continue

        lt_1500, gt_1500 = _extract_volume_tariffs(row.get("boxTariff"))
        target_region = _resolve_target_region(target_warehouse)
        target_region = _normalize_target_region_for_known_warehouses(target_warehouse, target_region)

        TransitDirectionTariff.objects.update_or_create(
            seller=seller,
            transit_warehouse=transit_warehouse,
            target_warehouse=target_warehouse,
            defaults={
                "target_region": target_region,
                "tariff_per_pallet": _to_float(row.get("palletTariff")),
                "box_price_per_liter_lt_1500": lt_1500,
                "box_price_per_liter_gt_1500": gt_1500,
                "delivery_eta": _normalize_text(row.get("activeFrom")) or None,
            },
        )
        synced += 1

    return synced


def sync_acceptance_coefficients(
    seller: SellerAccount,
    warehouse_ids: list[int] | None = None,
) -> int:
    """
    Синхронизирует WB common-api /tariffs/v1/acceptance/coefficients.

    Метод отдает коэффициенты по датам на ближайшие 14 дней, поэтому
    при регулярном вызове формируется исторический срез тарифов.
    """
    client = WBCommonClient(seller.api_token)
    rows = client.get_acceptance_coefficients(warehouse_ids=warehouse_ids)

    synced = 0
    for row in rows:
        row_date_raw = row.get("date")
        warehouse_id = row.get("warehouseID")
        box_type_id = row.get("boxTypeID")

        if not row_date_raw or warehouse_id is None:
            continue

        try:
            coeff_date = date.fromisoformat(str(row_date_raw)[:10])
        except ValueError:
            continue

        WbAcceptanceCoefficient.objects.update_or_create(
            seller=seller,
            coeff_date=coeff_date,
            warehouse_id=int(warehouse_id),
            box_type_id=box_type_id,
            defaults={
                "warehouse_name": _normalize_text(row.get("warehouseName")) or None,
                "coefficient": _to_float(row.get("coefficient")),
                "allow_unload": bool(row.get("allowUnload")),
                "is_sorting_center": bool(row.get("isSortingCenter")),
                "storage_coef": _to_float(row.get("storageCoef")),
                "delivery_coef": _to_float(row.get("deliveryCoef")),
                "delivery_base_liter": _to_float(row.get("deliveryBaseLiter")),
                "delivery_additional_liter": _to_float(row.get("deliveryAdditionalLiter")),
                "storage_base_liter": _to_float(row.get("storageBaseLiter")),
                "storage_additional_liter": _to_float(row.get("storageAdditionalLiter")),
                "raw_payload": row,
            },
        )
        synced += 1

    return synced
