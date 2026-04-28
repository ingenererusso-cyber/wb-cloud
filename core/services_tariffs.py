from __future__ import annotations

from datetime import date
from datetime import timedelta
from typing import Any

from django.utils import timezone
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

    Шушары относятся к СЗФО. Иногда WB/справочник возвращает некорректный округ
    (например, "Восток" или "Центральный федеральный округ"), поэтому для Шушар
    принудительно ставим СЗФО.
    """
    normalized_warehouse = _normalize_text(target_warehouse).lower().replace("ё", "е")
    if "шушар" in normalized_warehouse:
        return "Северо-Западный федеральный округ"
    return target_region


def sync_warehouse_tariffs(seller: SellerAccount, on_date: date | None = None) -> int:
    """
    Синхронизирует тарифы WB common-api /tariffs/box по складам.
    """
    client = WBCommonClient(seller.api_token_plain)
    tariff_date = on_date or date.today()
    warehouse_list = client.get_tariffs_box(on_date=tariff_date)

    prepared_rows: list[tuple[str, dict]] = []
    now_dt = timezone.now()
    for row in warehouse_list:
        warehouse_name = (row.get("warehouseName") or "").strip()
        if not warehouse_name:
            continue

        prepared_rows.append(
            (
                warehouse_name,
                {
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
                    "updated_at": now_dt,
                },
            )
        )

    if not prepared_rows:
        return 0

    existing_map = {
        str(item.warehouse_name): item
        for item in WbWarehouseTariff.objects.filter(
            seller=seller,
            tariff_date=tariff_date,
            warehouse_name__in=[row[0] for row in prepared_rows],
        )
    }
    to_create: list[WbWarehouseTariff] = []
    to_update: list[WbWarehouseTariff] = []
    update_fields = [
        "geo_name",
        "box_delivery_base",
        "box_delivery_coef_expr",
        "box_delivery_liter",
        "box_delivery_marketplace_base",
        "box_delivery_marketplace_coef_expr",
        "box_delivery_marketplace_liter",
        "box_storage_base",
        "box_storage_coef_expr",
        "box_storage_liter",
        "updated_at",
    ]
    for warehouse_name, defaults in prepared_rows:
        existing = existing_map.get(warehouse_name)
        if existing is None:
            to_create.append(
                WbWarehouseTariff(
                    seller=seller,
                    warehouse_name=warehouse_name,
                    tariff_date=tariff_date,
                    **defaults,
                )
            )
            continue
        for field_name in update_fields:
            setattr(existing, field_name, defaults[field_name])
        to_update.append(existing)

    if to_create:
        WbWarehouseTariff.objects.bulk_create(to_create, batch_size=2000)
    if to_update:
        WbWarehouseTariff.objects.bulk_update(to_update, update_fields, batch_size=2000)

    return len(prepared_rows)


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
    client = WBSuppliesClient(seller.api_token_plain)
    rows = client.get_transit_tariffs()

    prepared_rows: list[tuple[str, str, dict]] = []
    now_dt = timezone.now()
    for row in rows:
        transit_warehouse = _normalize_text(row.get("transitWarehouseName"))
        target_warehouse = _normalize_text(row.get("destinationWarehouseName"))
        if not transit_warehouse or not target_warehouse:
            continue

        lt_1500, gt_1500 = _extract_volume_tariffs(row.get("boxTariff"))
        target_region = _resolve_target_region(target_warehouse)
        target_region = _normalize_target_region_for_known_warehouses(target_warehouse, target_region)

        prepared_rows.append(
            (
                transit_warehouse,
                target_warehouse,
                {
                    "target_region": target_region,
                    "tariff_per_pallet": _to_float(row.get("palletTariff")),
                    "box_price_per_liter_lt_1500": lt_1500,
                    "box_price_per_liter_gt_1500": gt_1500,
                    "delivery_eta": _normalize_text(row.get("activeFrom")) or None,
                    "updated_at": now_dt,
                },
            )
        )

    if not prepared_rows:
        return 0

    existing_map = {
        (str(item.transit_warehouse), str(item.target_warehouse or "")): item
        for item in TransitDirectionTariff.objects.filter(
            seller=seller,
            transit_warehouse__in=[row[0] for row in prepared_rows],
            target_warehouse__in=[row[1] for row in prepared_rows],
        )
    }
    to_create: list[TransitDirectionTariff] = []
    to_update: list[TransitDirectionTariff] = []
    update_fields = [
        "target_region",
        "tariff_per_pallet",
        "box_price_per_liter_lt_1500",
        "box_price_per_liter_gt_1500",
        "delivery_eta",
        "updated_at",
    ]
    for transit_warehouse, target_warehouse, defaults in prepared_rows:
        key = (transit_warehouse, target_warehouse)
        existing = existing_map.get(key)
        if existing is None:
            to_create.append(
                TransitDirectionTariff(
                    seller=seller,
                    transit_warehouse=transit_warehouse,
                    target_warehouse=target_warehouse,
                    **defaults,
                )
            )
            continue
        for field_name in update_fields:
            setattr(existing, field_name, defaults[field_name])
        to_update.append(existing)

    if to_create:
        TransitDirectionTariff.objects.bulk_create(to_create, batch_size=2000)
    if to_update:
        TransitDirectionTariff.objects.bulk_update(to_update, update_fields, batch_size=2000)

    return len(prepared_rows)


def sync_acceptance_coefficients(
    seller: SellerAccount,
    warehouse_ids: list[int] | None = None,
) -> int:
    """
    Синхронизирует WB common-api /tariffs/v1/acceptance/coefficients.

    Метод отдает коэффициенты по датам на ближайшие 14 дней, поэтому
    при регулярном вызове формируется исторический срез тарифов.
    """
    client = WBCommonClient(seller.api_token_plain)
    rows = client.get_acceptance_coefficients(warehouse_ids=warehouse_ids)
    prepared_rows: list[dict[str, Any]] = []
    coeff_dates: set[date] = set()
    warehouse_ids_set: set[int] = set()
    box_type_ids_set: set[int] = set()

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

        warehouse_id_int = int(warehouse_id)
        box_type_id_int = int(box_type_id) if box_type_id is not None else None
        coeff_dates.add(coeff_date)
        warehouse_ids_set.add(warehouse_id_int)
        if box_type_id_int is not None:
            box_type_ids_set.add(box_type_id_int)

        prepared_rows.append(
            {
                "coeff_date": coeff_date,
                "warehouse_id": warehouse_id_int,
                "box_type_id": box_type_id_int,
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
            }
        )

    if not prepared_rows:
        return 0

    existing_qs = WbAcceptanceCoefficient.objects.filter(
        seller=seller,
        coeff_date__in=list(coeff_dates),
        warehouse_id__in=list(warehouse_ids_set),
    )
    if box_type_ids_set:
        existing_qs = existing_qs.filter(box_type_id__in=list(box_type_ids_set) + [None])
    else:
        existing_qs = existing_qs.filter(box_type_id__isnull=True)

    existing_map = {
        (row.coeff_date, row.warehouse_id, row.box_type_id): row
        for row in existing_qs
    }

    to_create: list[WbAcceptanceCoefficient] = []
    to_update: list[WbAcceptanceCoefficient] = []
    update_fields = [
        "warehouse_name",
        "coefficient",
        "allow_unload",
        "is_sorting_center",
        "storage_coef",
        "delivery_coef",
        "delivery_base_liter",
        "delivery_additional_liter",
        "storage_base_liter",
        "storage_additional_liter",
        "raw_payload",
    ]

    for payload in prepared_rows:
        key = (payload["coeff_date"], payload["warehouse_id"], payload["box_type_id"])
        existing = existing_map.get(key)
        if existing is None:
            to_create.append(
                WbAcceptanceCoefficient(
                    seller=seller,
                    coeff_date=payload["coeff_date"],
                    warehouse_id=payload["warehouse_id"],
                    box_type_id=payload["box_type_id"],
                    warehouse_name=payload["warehouse_name"],
                    coefficient=payload["coefficient"],
                    allow_unload=payload["allow_unload"],
                    is_sorting_center=payload["is_sorting_center"],
                    storage_coef=payload["storage_coef"],
                    delivery_coef=payload["delivery_coef"],
                    delivery_base_liter=payload["delivery_base_liter"],
                    delivery_additional_liter=payload["delivery_additional_liter"],
                    storage_base_liter=payload["storage_base_liter"],
                    storage_additional_liter=payload["storage_additional_liter"],
                    raw_payload=payload["raw_payload"],
                )
            )
            continue

        for field_name in update_fields:
            setattr(existing, field_name, payload[field_name])
        to_update.append(existing)

    if to_create:
        WbAcceptanceCoefficient.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        WbAcceptanceCoefficient.objects.bulk_update(to_update, update_fields, batch_size=500)

    return len(prepared_rows)
