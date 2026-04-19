from wb_api.client import WBContentClient
from core.models import Product, SellerAccount
from django.utils.dateparse import parse_datetime


def _parse_wb_datetime(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    return parsed


def sync_products_content(seller: SellerAccount) -> int:
    """
    Синхронизирует карточки товаров и сохраняет вес/объем в Product.
    """
    client = WBContentClient(seller.api_token_plain)
    cards = client.get_cards_list(limit=100)

    synced = 0
    for card in cards:
        payload = client._extract_card_payload(card)
        nm_id = payload.get("nm_id")
        vendor_code = (payload.get("vendor_code") or "").strip()

        if nm_id is None or not vendor_code:
            continue

        Product.objects.update_or_create(
            seller=seller,
            nm_id=nm_id,
            vendor_code=vendor_code,
            defaults={
                "title": payload.get("title"),
                "brand": payload.get("brand"),
                "subject_id": payload.get("subject_id"),
                "subject_name": payload.get("subject_name"),
                "photo_url": payload.get("photo_url"),
                "weight_kg": payload.get("weight_kg"),
                "length_cm": payload.get("length_cm"),
                "width_cm": payload.get("width_cm"),
                "height_cm": payload.get("height_cm"),
                "volume_liters": payload.get("volume_liters"),
                "wb_created_at": _parse_wb_datetime(payload.get("wb_created_at")),
                "wb_updated_at": _parse_wb_datetime(payload.get("wb_updated_at")),
            },
        )
        synced += 1

    return synced
