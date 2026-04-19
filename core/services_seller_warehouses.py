from core.models import SellerAccount, SellerWarehouse
from wb_api.client import WBMarketplaceClient


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

    synced = 0
    for row in warehouses:
        seller_warehouse_id = _to_int(row.get("id"))
        if seller_warehouse_id is None:
            continue

        SellerWarehouse.objects.update_or_create(
            seller=seller,
            seller_warehouse_id=seller_warehouse_id,
            defaults={
                "office_id": _to_int(row.get("officeId")),
                "name": (row.get("name") or "").strip() or f"Склад #{seller_warehouse_id}",
                "cargo_type": _to_int(row.get("cargoType")),
                "delivery_type": _to_int(row.get("deliveryType")),
                "is_deleting": bool(row.get("isDeleting")),
                "is_processing": bool(row.get("isProcessing")),
                "raw_payload": row,
            },
        )
        synced += 1

    return synced

