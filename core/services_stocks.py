from wb_api.client import WBStocksSupplierClient
from .models import WarehouseStockDetailed, SellerAccount


def sync_supplier_stocks(seller: SellerAccount):
    client = WBStocksSupplierClient(seller.api_token_plain)  # убедись, что токен stats API

    result = client.get_supplier_stocks()
    prepared_rows: list[tuple[int, str, str, str, int]] = []
    for r in result:
        nm_id = r.get("nmId")
        supplier_article = r.get("supplierArticle")
        tech_size = r.get("techSize")
        warehouse_name = r.get("warehouseName")
        if nm_id is None or supplier_article is None or tech_size is None or warehouse_name is None:
            continue
        prepared_rows.append(
            (
                int(nm_id),
                str(supplier_article),
                str(tech_size),
                str(warehouse_name),
                int(r.get("quantity") or 0),
            )
        )

    if not prepared_rows:
        return 0

    existing_map = {
        (int(item.nm_id), item.supplier_article, item.tech_size, item.warehouse_name): item
        for item in WarehouseStockDetailed.objects.filter(seller=seller)
    }
    to_create: list[WarehouseStockDetailed] = []
    to_update: list[WarehouseStockDetailed] = []
    for nm_id, supplier_article, tech_size, warehouse_name, quantity in prepared_rows:
        key = (nm_id, supplier_article, tech_size, warehouse_name)
        existing = existing_map.get(key)
        if existing is None:
            to_create.append(
                WarehouseStockDetailed(
                    seller=seller,
                    nm_id=nm_id,
                    supplier_article=supplier_article,
                    tech_size=tech_size,
                    warehouse_name=warehouse_name,
                    quantity=quantity,
                )
            )
            continue
        existing.quantity = quantity
        to_update.append(existing)

    if to_create:
        WarehouseStockDetailed.objects.bulk_create(to_create, batch_size=2000)
    if to_update:
        WarehouseStockDetailed.objects.bulk_update(to_update, ["quantity"], batch_size=2000)

    return len(prepared_rows)
