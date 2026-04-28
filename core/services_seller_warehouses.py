from core.models import SellerAccount, SellerWarehouse
from wb_api.client import WBMarketplaceClient
from django.utils import timezone


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_seller_warehouses(seller: SellerAccount) -> int:
    """
    Синхронизация списка складов продавца (Marketplace API /api/v3/warehouses).
    """
    client = WBMarketplaceClient(seller.api_token_plain)
    warehouses = client.get_seller_warehouses()

    prepared_rows: list[tuple[int, dict]] = []
    now_dt = timezone.now()
    for row in warehouses:
        seller_warehouse_id = _to_int(row.get("id"))
        if seller_warehouse_id is None:
            continue

        prepared_rows.append(
            (
                int(seller_warehouse_id),
                {
                    "office_id": _to_int(row.get("officeId")),
                    "name": (row.get("name") or "").strip() or f"Склад #{seller_warehouse_id}",
                    "cargo_type": _to_int(row.get("cargoType")),
                    "delivery_type": _to_int(row.get("deliveryType")),
                    "is_deleting": bool(row.get("isDeleting")),
                    "is_processing": bool(row.get("isProcessing")),
                    "raw_payload": row,
                    "updated_at": now_dt,
                },
            )
        )

    if not prepared_rows:
        return 0

    existing_map = {
        int(item.seller_warehouse_id): item
        for item in SellerWarehouse.objects.filter(
            seller=seller,
            seller_warehouse_id__in=[row[0] for row in prepared_rows],
        )
    }
    to_create: list[SellerWarehouse] = []
    to_update: list[SellerWarehouse] = []
    update_fields = [
        "office_id",
        "name",
        "cargo_type",
        "delivery_type",
        "is_deleting",
        "is_processing",
        "raw_payload",
        "updated_at",
    ]
    for seller_warehouse_id, defaults in prepared_rows:
        existing = existing_map.get(seller_warehouse_id)
        if existing is None:
            to_create.append(
                SellerWarehouse(
                    seller=seller,
                    seller_warehouse_id=seller_warehouse_id,
                    **defaults,
                )
            )
            continue
        for field_name in update_fields:
            setattr(existing, field_name, defaults[field_name])
        to_update.append(existing)

    if to_create:
        SellerWarehouse.objects.bulk_create(to_create, batch_size=2000)
    if to_update:
        SellerWarehouse.objects.bulk_update(to_update, update_fields, batch_size=2000)

    return len(prepared_rows)

