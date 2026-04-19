from __future__ import annotations

import time

from core.models import ProductSizePrice, SellerAccount
from wb_api.client import WBDiscountsPricesClient


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(" ", "").replace(",", ".")
        if not value or value in {"-", "—", "null", "None"}:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def sync_product_size_prices(
    seller: SellerAccount,
    page_limit: int = 1000,
    request_pause_seconds: float = 0.62,
) -> int:
    """
    Синк цен по размерам товара из discounts-prices API.

    Источник: массовый метод /api/v2/list/goods/filter
    (все товары продавца сразу, c пагинацией limit/offset).
    """
    client = WBDiscountsPricesClient(seller.api_token_plain)
    synced_rows = 0
    offset = 0
    page = 0
    while True:
        payload = client.get_goods_with_prices(limit=page_limit, offset=offset)
        rows = ((payload or {}).get("data") or {}).get("listGoods") or []
        if not isinstance(rows, list) or not rows:
            break

        for item in rows:
            item_nm_id = _to_int(item.get("nmID"))
            if item_nm_id is None:
                continue

            # В этом методе размеры приходят массивом.
            sizes = item.get("sizes") or []
            if not isinstance(sizes, list):
                sizes = []

            if not sizes:
                # Фолбэк: сохраняем одну строку по самому товару, если размеров нет.
                size_id = _to_int(item.get("sizeID")) or 0
                ProductSizePrice.objects.update_or_create(
                    seller=seller,
                    nm_id=item_nm_id,
                    size_id=size_id,
                    defaults={
                        "chrt_id": _to_int(item.get("sizeID")),
                        "vendor_code": (item.get("vendorCode") or "").strip() or None,
                        "tech_size_name": (item.get("techSizeName") or "").strip() or None,
                        "price": _to_float(item.get("price")),
                        "discounted_price": _to_float(item.get("discountedPrice")),
                        "club_discounted_price": _to_float(item.get("clubDiscountedPrice")),
                        "currency_iso_code_4217": (item.get("currencyIsoCode4217") or "").strip() or None,
                        "discount_percent": _to_float(item.get("discount")),
                        "club_discount_percent": _to_float(item.get("clubDiscount")),
                        "editable_size_price": bool(item.get("editableSizePrice")),
                        "is_bad_turnover": item.get("isBadTurnover") if "isBadTurnover" in item else None,
                        "raw_payload": item,
                    },
                )
                synced_rows += 1
                continue

            for size in sizes:
                size_id = _to_int(size.get("sizeID")) or _to_int(size.get("chrtID")) or _to_int(size.get("chrtId"))
                if size_id is None:
                    continue

                ProductSizePrice.objects.update_or_create(
                    seller=seller,
                    nm_id=item_nm_id,
                    size_id=size_id,
                    defaults={
                        "chrt_id": _to_int(size.get("chrtID")) or _to_int(size.get("chrtId")) or size_id,
                        "vendor_code": (item.get("vendorCode") or "").strip() or None,
                        "tech_size_name": (size.get("techSizeName") or size.get("techSize") or "").strip() or None,
                        "price": _to_float(size.get("price", item.get("price"))),
                        "discounted_price": _to_float(size.get("discountedPrice", item.get("discountedPrice"))),
                        "club_discounted_price": _to_float(size.get("clubDiscountedPrice", item.get("clubDiscountedPrice"))),
                        "currency_iso_code_4217": (
                            size.get("currencyIsoCode4217")
                            or item.get("currencyIsoCode4217")
                            or ""
                        ).strip() or None,
                        "discount_percent": _to_float(size.get("discount", item.get("discount"))),
                        "club_discount_percent": _to_float(size.get("clubDiscount", item.get("clubDiscount"))),
                        "editable_size_price": bool(
                            size.get("editableSizePrice", item.get("editableSizePrice"))
                        ),
                        "is_bad_turnover": (
                            size.get("isBadTurnover")
                            if "isBadTurnover" in size
                            else item.get("isBadTurnover")
                        ),
                        "raw_payload": {"item": item, "size": size},
                    },
                )
                synced_rows += 1

        page += 1
        if len(rows) < page_limit:
            break
        offset += page_limit
        if request_pause_seconds > 0:
            time.sleep(request_pause_seconds)

    return synced_rows
