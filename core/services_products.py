from wb_api.client import WBContentClient
from core.models import Product, SellerAccount


def sync_products_content(seller: SellerAccount) -> int:
    """
    Синхронизирует карточки товаров и сохраняет вес/объем в Product.
    """
    client = WBContentClient(seller.api_token)
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
                "brand": payload.get("brand"),
                "weight_kg": payload.get("weight_kg"),
                "volume_liters": payload.get("volume_liters"),
            },
        )
        synced += 1

    return synced
