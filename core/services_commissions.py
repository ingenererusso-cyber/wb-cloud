from __future__ import annotations

import time
from django.db import OperationalError

from core.models import SellerAccount, WbCategoryCommission
from wb_api.client import WBCommonClient


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


def _update_or_create_with_db_retry(*, seller, locale, subject_id, defaults, attempts: int = 10):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return WbCategoryCommission.objects.update_or_create(
                seller=seller,
                locale=locale,
                subject_id=subject_id,
                defaults=defaults,
            )
        except OperationalError as exc:
            text = str(exc).lower()
            if "database is locked" not in text and "database table is locked" not in text:
                raise
            last_exc = exc
            time.sleep(min(2.5, 0.2 * attempt))
    if last_exc:
        raise last_exc
    return WbCategoryCommission.objects.update_or_create(
        seller=seller,
        locale=locale,
        subject_id=subject_id,
        defaults=defaults,
    )


def sync_category_commissions(seller: SellerAccount, locale: str = "ru") -> int:
    """
    Синк комиссий WB по категориям товаров.
    """
    client = WBCommonClient(seller.api_token_plain)
    payload = client.get_category_commissions(locale=locale)
    rows = payload.get("report") or []
    if not isinstance(rows, list):
        return 0

    synced = 0
    for row in rows:
        subject_id = _to_int(row.get("subjectID"))
        if subject_id is None:
            continue

        _update_or_create_with_db_retry(
            seller=seller,
            locale=locale,
            subject_id=subject_id,
            defaults={
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
            },
        )
        synced += 1

    return synced
