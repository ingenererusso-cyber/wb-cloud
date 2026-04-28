from wb_api.client import WBContentClient
from core.models import Product, SellerAccount
from django.utils.dateparse import parse_datetime

SQL_IN_CHUNK_SIZE = 10_000


def _parse_wb_datetime(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    return parsed


def _iter_chunks(items: list, size: int):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def sync_products_content(seller: SellerAccount) -> int:
    """
    Синхронизирует карточки товаров и сохраняет вес/объем в Product.
    """
    client = WBContentClient(seller.api_token_plain)
    cards = client.get_cards_list(limit=100)

    prepared_rows: list[tuple[int, str, dict]] = []
    for card in cards:
        payload = client._extract_card_payload(card)
        nm_id = payload.get("nm_id")
        vendor_code = (payload.get("vendor_code") or "").strip()

        if nm_id is None or not vendor_code:
            continue

        prepared_rows.append(
            (
                int(nm_id),
                vendor_code,
                {
                    "imt_id": payload.get("imt_id"),
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
        )

    if not prepared_rows:
        return 0

    nm_ids = sorted({row[0] for row in prepared_rows})
    existing_map: dict[tuple[int, str], Product] = {}
    for nm_chunk in _iter_chunks(nm_ids, SQL_IN_CHUNK_SIZE):
        for item in Product.objects.filter(seller=seller, nm_id__in=nm_chunk):
            existing_map[(int(item.nm_id), str(item.vendor_code or "").strip())] = item

    to_create: list[Product] = []
    to_update: list[Product] = []
    update_fields = [
        "imt_id",
        "title",
        "brand",
        "subject_id",
        "subject_name",
        "photo_url",
        "weight_kg",
        "length_cm",
        "width_cm",
        "height_cm",
        "volume_liters",
        "wb_created_at",
        "wb_updated_at",
    ]
    for nm_id, vendor_code, defaults in prepared_rows:
        key = (nm_id, vendor_code)
        existing = existing_map.get(key)
        if existing is None:
            to_create.append(Product(seller=seller, nm_id=nm_id, vendor_code=vendor_code, **defaults))
            continue
        for field_name in update_fields:
            setattr(existing, field_name, defaults[field_name])
        to_update.append(existing)

    if to_create:
        Product.objects.bulk_create(to_create, batch_size=2000)
    if to_update:
        Product.objects.bulk_update(to_update, update_fields, batch_size=2000)

    return len(prepared_rows)
