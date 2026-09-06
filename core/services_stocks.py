import time

from wb_api.client import WBAnalyticsClient
from .models import Product, ProductCardSize, ProductSizePrice, WarehouseStockDetailed, SellerAccount
from .services_fbs_stocks import _sync_product_card_sizes


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_size_meta_map(seller: SellerAccount) -> dict[int, dict[str, str | None]]:
    size_meta: dict[int, dict[str, str | None]] = {}

    for item in ProductCardSize.objects.filter(seller=seller).values(
        "chrt_id",
        "vendor_code",
        "tech_size",
        "wb_size",
    ):
        chrt_id = _to_int(item.get("chrt_id"))
        if chrt_id is None:
            continue
        size_meta[chrt_id] = {
            "supplier_article": (item.get("vendor_code") or "").strip() or None,
            "tech_size": (item.get("tech_size") or item.get("wb_size") or "").strip() or None,
        }

    for item in ProductSizePrice.objects.filter(seller=seller).exclude(chrt_id__isnull=True).values(
        "chrt_id",
        "vendor_code",
        "tech_size_name",
    ):
        chrt_id = _to_int(item.get("chrt_id"))
        if chrt_id is None or chrt_id in size_meta:
            continue
        size_meta[chrt_id] = {
            "supplier_article": (item.get("vendor_code") or "").strip() or None,
            "tech_size": (item.get("tech_size_name") or "").strip() or None,
        }

    return size_meta


def _build_product_article_map(seller: SellerAccount) -> dict[int, str]:
    product_article: dict[int, str] = {}
    for item in Product.objects.filter(seller=seller).values("nm_id", "vendor_code"):
        nm_id = _to_int(item.get("nm_id"))
        vendor_code = (item.get("vendor_code") or "").strip()
        if nm_id is not None and vendor_code and nm_id not in product_article:
            product_article[nm_id] = vendor_code
    return product_article


def _iter_wb_warehouse_stock_rows(
    client: WBAnalyticsClient,
    *,
    page_limit: int,
    request_pause_seconds: float,
):
    offset = 0
    while True:
        rows = client.get_wb_warehouse_stocks(limit=page_limit, offset=offset)
        if not rows:
            break
        yield from rows
        if len(rows) < page_limit:
            break
        offset += page_limit
        time.sleep(max(0.0, float(request_pause_seconds)))


def sync_supplier_stocks(
    seller: SellerAccount,
    page_limit: int = 250000,
    request_pause_seconds: float = 20.1,
):
    # Новый Analytics endpoint возвращает chrtId, но не supplierArticle/techSize.
    _sync_product_card_sizes(seller)

    client = WBAnalyticsClient(seller.api_token_plain)
    size_meta = _build_size_meta_map(seller)
    product_article = _build_product_article_map(seller)

    prepared_rows: list[tuple[int, str, str, str, int]] = []
    for r in _iter_wb_warehouse_stock_rows(
        client,
        page_limit=page_limit,
        request_pause_seconds=request_pause_seconds,
    ):
        nm_id = r.get("nmId")
        chrt_id = _to_int(r.get("chrtId"))
        warehouse_name = r.get("warehouseName")
        quantity = r.get("quantity")
        if nm_id is None or chrt_id is None or warehouse_name is None:
            continue
        meta = size_meta.get(chrt_id) or {}
        supplier_article = meta.get("supplier_article") or product_article.get(int(nm_id)) or str(nm_id)
        tech_size = meta.get("tech_size") or str(chrt_id)
        prepared_rows.append(
            (
                int(nm_id),
                str(supplier_article),
                str(tech_size),
                str(warehouse_name),
                int(quantity or 0),
            )
        )

    existing_map = {
        (int(item.nm_id), item.supplier_article, item.tech_size, item.warehouse_name): item
        for item in WarehouseStockDetailed.objects.filter(seller=seller)
    }
    actual_keys = {
        (nm_id, supplier_article, tech_size, warehouse_name)
        for nm_id, supplier_article, tech_size, warehouse_name, _quantity in prepared_rows
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
    stale_ids = [
        item.id
        for key, item in existing_map.items()
        if key not in actual_keys
    ]
    if stale_ids:
        WarehouseStockDetailed.objects.filter(id__in=stale_ids).delete()

    return len(prepared_rows)
