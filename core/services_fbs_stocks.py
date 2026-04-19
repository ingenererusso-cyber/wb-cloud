from __future__ import annotations

from typing import Dict, Iterable, List

from core.models import ProductCardSize, SellerAccount, SellerFbsStock, SellerWarehouse
from wb_api.client import WBContentClient, WBMarketplaceClient


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunks(items: List[int], size: int) -> Iterable[List[int]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _sync_product_card_sizes(seller: SellerAccount) -> int:
    content_client = WBContentClient(seller.api_token_plain)
    cards = content_client.get_cards_list(limit=100)

    synced = 0
    for card in cards:
        nm_id = _to_int(card.get("nmID"))
        vendor_code = (card.get("vendorCode") or "").strip() or None
        title = (card.get("title") or "").strip() or None
        sizes = card.get("sizes") or []
        if not isinstance(sizes, list):
            continue

        for size in sizes:
            if not isinstance(size, dict):
                continue
            chrt_id = _to_int(size.get("chrtID"))
            if chrt_id is None:
                continue
            ProductCardSize.objects.update_or_create(
                seller=seller,
                chrt_id=chrt_id,
                defaults={
                    "nm_id": nm_id,
                    "vendor_code": vendor_code,
                    "title": title,
                    "tech_size": (size.get("techSize") or "").strip() or None,
                    "wb_size": (size.get("wbSize") or "").strip() or None,
                },
            )
            synced += 1
    return synced


def sync_seller_fbs_stocks(seller: SellerAccount, batch_size: int = 1000) -> Dict[str, int]:
    """
    Синхронизация FBS-остатков:
    1) обновляем размеры карточек (chrt_id),
    2) для каждого FBS-склада продавца запрашиваем остатки по chrtIds.
    """
    sizes_synced = _sync_product_card_sizes(seller)

    warehouses = list(
        SellerWarehouse.objects.filter(seller=seller, delivery_type=1).order_by("seller_warehouse_id")
    )
    chrt_ids = list(
        ProductCardSize.objects.filter(seller=seller).values_list("chrt_id", flat=True).order_by("chrt_id")
    )
    if not warehouses or not chrt_ids:
        SellerFbsStock.objects.filter(seller=seller).delete()
        return {
            "sizes_synced": sizes_synced,
            "warehouses": len(warehouses),
            "stocks_rows": 0,
        }

    market_client = WBMarketplaceClient(seller.api_token_plain)
    rows_to_create: List[SellerFbsStock] = []

    for wh in warehouses:
        for chunk in _chunks(chrt_ids, max(1, int(batch_size))):
            payload = market_client.get_seller_warehouse_stocks(
                warehouse_id=wh.seller_warehouse_id,
                chrt_ids=chunk,
            )
            stocks = payload.get("stocks") or []
            by_chrt: Dict[int, Dict] = {}
            if isinstance(stocks, list):
                for item in stocks:
                    if not isinstance(item, dict):
                        continue
                    chrt_id = _to_int(item.get("chrtId"))
                    if chrt_id is None:
                        continue
                    by_chrt[chrt_id] = item

            for chrt_id in chunk:
                raw_item = by_chrt.get(chrt_id) or {"chrtId": chrt_id, "amount": 0}
                rows_to_create.append(
                    SellerFbsStock(
                        seller=seller,
                        seller_warehouse=wh,
                        warehouse_name=wh.name,
                        chrt_id=chrt_id,
                        amount=_to_int(raw_item.get("amount")) or 0,
                        raw_payload=raw_item,
                    )
                )

    SellerFbsStock.objects.filter(seller=seller).delete()
    SellerFbsStock.objects.bulk_create(rows_to_create, batch_size=2000)

    return {
        "sizes_synced": sizes_synced,
        "warehouses": len(warehouses),
        "stocks_rows": len(rows_to_create),
    }


def apply_fbs_stock_updates(seller: SellerAccount, changes: List[Dict], wb_batch_size: int = 1000) -> Dict[str, int]:
    """
    Применяет изменения остатков FBS:
    - отправляет в WB grouped by seller_warehouse_id;
    - после успеха обновляет локальную таблицу SellerFbsStock.
    """
    if not changes:
        return {"updated_rows": 0, "warehouses_touched": 0}

    grouped: Dict[int, List[Dict]] = {}
    row_map: Dict[tuple[int, int], int] = {}

    for item in changes:
        warehouse_id = _to_int(item.get("seller_warehouse_id"))
        chrt_id = _to_int(item.get("chrt_id"))
        amount = _to_int(item.get("amount"))
        if warehouse_id is None or chrt_id is None or amount is None:
            continue
        amount = max(0, min(100000, amount))
        grouped.setdefault(warehouse_id, []).append({"chrtId": chrt_id, "amount": amount})
        row_map[(warehouse_id, chrt_id)] = amount

    if not grouped:
        return {"updated_rows": 0, "warehouses_touched": 0}

    market_client = WBMarketplaceClient(seller.api_token_plain)
    for warehouse_id, stocks in grouped.items():
        for chunk in _chunks(stocks, max(1, int(wb_batch_size))):
            market_client.update_seller_warehouse_stocks(
                warehouse_id=warehouse_id,
                stocks=chunk,
            )

    # Обновляем локальные данные после успешной отправки во все склады.
    wh_by_id = {
        row.seller_warehouse_id: row
        for row in SellerWarehouse.objects.filter(seller=seller, seller_warehouse_id__in=grouped.keys())
    }
    updated_rows = 0
    for (warehouse_id, chrt_id), amount in row_map.items():
        wh = wh_by_id.get(warehouse_id)
        if not wh:
            continue
        count = (
            SellerFbsStock.objects
            .filter(seller=seller, seller_warehouse=wh, chrt_id=chrt_id)
            .update(amount=amount)
        )
        updated_rows += int(count or 0)

    return {
        "updated_rows": updated_rows,
        "warehouses_touched": len(grouped),
    }
