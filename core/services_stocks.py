from wb_api.client import WBStocksSupplierClient
from .models import WarehouseStockDetailed, SellerAccount


def sync_supplier_stocks(seller: SellerAccount):
    client = WBStocksSupplierClient(seller.api_token)  # убедись, что токен stats API

    result = client.get_supplier_stocks()
    synced = 0

    for r in result:
        WarehouseStockDetailed.objects.update_or_create(
            seller=seller,
            nm_id=r["nmId"],
            supplier_article=r["supplierArticle"],
            tech_size=r["techSize"],
            warehouse_name=r["warehouseName"],
            defaults={"quantity": r["quantity"]},
        )
        synced += 1

    return synced
