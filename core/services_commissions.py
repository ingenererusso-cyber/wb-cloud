from __future__ import annotations

from core.models import SellerAccount, WbCategoryCommission
from wb_api.client import WBCommonClient
from django.utils import timezone

SQL_IN_CHUNK_SIZE = 10_000

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


def _iter_chunks(items: list, size: int):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def sync_category_commissions(seller: SellerAccount, locale: str = "ru") -> int:
    """
    Синк комиссий WB по категориям товаров.
    """
    client = WBCommonClient(seller.api_token_plain)
    payload = client.get_category_commissions(locale=locale)
    rows = payload.get("report") or []
    if not isinstance(rows, list):
        return 0

    prepared_rows: list[tuple[int, dict]] = []
    now_dt = timezone.now()
    for row in rows:
        subject_id = _to_int(row.get("subjectID"))
        if subject_id is None:
            continue

        prepared_rows.append(
            (
                int(subject_id),
                {
                    "subject_name": (row.get("subjectName") or "").strip() or None,
                    "parent_id": _to_int(row.get("parentID")),
                    "parent_name": (row.get("parentName") or "").strip() or None,
                    "kgvp_booking": _to_float(row.get("kgvpBooking")),
                    "kgvp_marketplace": _to_float(row.get("kgvpMarketplace")),
                    "kgvp_pickup": _to_float(row.get("kgvpPickup")),
                    "kgvp_supplier": _to_float(row.get("kgvpSupplier")),
                    "kgvp_supplier_express": _to_float(row.get("kgvpSupplierExpress")),
                    "paid_storage_kgvp": _to_float(row.get("paidStorageKgvp")),
                    "raw_payload": row,
                    "updated_at": now_dt,
                },
            )
        )

    if not prepared_rows:
        return 0

    subject_ids = [row[0] for row in prepared_rows]
    existing_map: dict[int, WbCategoryCommission] = {}
    for chunk in _iter_chunks(subject_ids, SQL_IN_CHUNK_SIZE):
        for item in WbCategoryCommission.objects.filter(
            seller=seller,
            locale=locale,
            subject_id__in=chunk,
        ):
            existing_map[int(item.subject_id)] = item

    to_create: list[WbCategoryCommission] = []
    to_update: list[WbCategoryCommission] = []
    update_fields = [
        "subject_name",
        "parent_id",
        "parent_name",
        "kgvp_booking",
        "kgvp_marketplace",
        "kgvp_pickup",
        "kgvp_supplier",
        "kgvp_supplier_express",
        "paid_storage_kgvp",
        "raw_payload",
        "updated_at",
    ]
    for subject_id, defaults in prepared_rows:
        existing = existing_map.get(subject_id)
        if existing is None:
            to_create.append(
                WbCategoryCommission(
                    seller=seller,
                    locale=locale,
                    subject_id=subject_id,
                    **defaults,
                )
            )
            continue
        for field_name in update_fields:
            setattr(existing, field_name, defaults[field_name])
        to_update.append(existing)

    if to_create:
        WbCategoryCommission.objects.bulk_create(to_create, batch_size=2000)
    if to_update:
        WbCategoryCommission.objects.bulk_update(to_update, update_fields, batch_size=2000)

    return len(prepared_rows)
