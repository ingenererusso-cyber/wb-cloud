from datetime import datetime, timedelta
import time
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from wb_api.client import WBOrdersSupplierClient, WBSalesSupplierClient
from .models import Order, SellerAccount
from core.services.localization import determine_locality

INITIAL_SYNC_WEEKS = 25
INITIAL_SYNC_DAYS = INITIAL_SYNC_WEEKS * 7


def _extract_order_price_from_row(row: dict) -> float | None:
    for key in (
        "priceWithDisc",
        "priceWithDiscount",
        "price",
        "totalPrice",
        "total_price",
        "retailPrice",
        "discountedPrice",
    ):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _to_aware_datetime(value, default_tz):
    if isinstance(value, datetime):
        dt = value
    elif value:
        dt = parse_datetime(value)
    else:
        dt = None

    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt, default_tz)
    return dt


def sync_fbw_orders(seller: SellerAccount, days_back: int = INITIAL_SYNC_DAYS):
    """
    Загружает заказы за последние days_back дней.
    """

    client = WBOrdersSupplierClient(seller.api_token_plain)
    default_tz = timezone.get_default_timezone()

    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    rows = client.get_orders(date_from=date_from)

    for r in rows:
        srid = r.get("srid")
        warehouse_name = r.get("warehouseName")
        warehouse_type = r.get("warehouseType")
        nm_id = r.get("nmId")
        if not srid or not warehouse_name or warehouse_type is None or nm_id is None:
            continue

        is_fbw = r.get("warehouseType") == "Склад WB"
        oblast_okrug_name = (r.get("oblastOkrugName") or "").strip()
        if not oblast_okrug_name:
            oblast_okrug_name = (r.get("countryName") or "").strip()

        is_local = determine_locality(warehouse_name, oblast_okrug_name) if is_fbw else False
        order_date = _to_aware_datetime(r.get("date"), default_tz)
        last_change_date = _to_aware_datetime(r.get("lastChangeDate"), default_tz)

        Order.objects.update_or_create(
            seller=seller,
            srid=srid,  # уникальный ID заказа в рамках seller
            defaults={
                "nm_id": nm_id,
                "supplier_article": r.get("supplierArticle"),
                "tech_size": r.get("techSize"),
                "warehouse_name": warehouse_name,
                "warehouse_type": warehouse_type,
                "region_name": r.get("regionName"),
                "country_name": r.get("countryName"),
                "oblast_okrug_name": oblast_okrug_name or None,
                "is_cancel": bool(r.get("isCancel", False)),
                "is_buyout": False,
                "order_price": _extract_order_price_from_row(r),
                "finished_price": r.get("finishedPrice"),
                "order_date": order_date,
                "last_change_date": last_change_date,
                "is_local": is_local,
            }
        )

    return len(rows)


def _normalize_sales_cursor(raw_value) -> datetime | None:
    if not raw_value:
        return None
    dt = parse_datetime(str(raw_value))
    if not dt:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _format_sales_cursor(dt: datetime) -> str:
    local_dt = timezone.localtime(dt, timezone=timezone.get_default_timezone())
    # WB принимает RFC3339 в московском времени
    return local_dt.replace(tzinfo=None).isoformat(timespec="seconds")


def _is_sales_return_row(row: dict) -> bool:
    sale_id = str(row.get("saleID") or "").strip().upper()
    if sale_id.startswith("R"):
        return True
    return False


def _is_sales_buyout_row(row: dict) -> bool:
    sale_id = str(row.get("saleID") or "").strip().upper()
    if sale_id.startswith("S"):
        return True
    return False


def sync_sales_buyout_flags(seller: SellerAccount, overlap_minutes: int = 90, max_pages: int = 20) -> dict:
    """
    Инкрементально обновляет флаги выкупа/возврата заказов по /supplier/sales.
    """
    client = WBSalesSupplierClient(seller.api_token_plain)
    meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
    sync_state = meta.get("sales_sync") if isinstance(meta.get("sales_sync"), dict) else {}
    last_change_raw = sync_state.get("last_change_date")
    last_change_dt = _normalize_sales_cursor(last_change_raw)
    bootstrap_completed = bool(sync_state.get("bootstrap_completed"))
    if not bootstrap_completed:
        # Первый полноценный прогон: подтягиваем историю продаж/возвратов WB за 25 недель,
        # чтобы buyout_date был заполнен не только у последних изменений.
        date_from_dt = timezone.now() - timedelta(days=INITIAL_SYNC_DAYS)
    else:
        if last_change_dt is None:
            last_change_dt = timezone.now() - timedelta(days=INITIAL_SYNC_DAYS)
        date_from_dt = last_change_dt - timedelta(minutes=max(0, int(overlap_minutes)))

    total_rows = 0
    buyout_marks = 0
    return_marks = 0
    processed_srids: set[str] = set()
    pages = 0
    next_cursor = date_from_dt

    while pages < max_pages:
        pages += 1
        rows = client.get_sales(date_from=_format_sales_cursor(next_cursor), flag=0)
        if not rows:
            break
        total_rows += len(rows)

        latest_change_dt = next_cursor
        for row in rows:
            srid = str(row.get("srid") or "").strip()
            if not srid:
                continue
            processed_srids.add(srid)
            row_change_dt = _normalize_sales_cursor(row.get("lastChangeDate"))
            if row_change_dt and row_change_dt > latest_change_dt:
                latest_change_dt = row_change_dt

            is_return = _is_sales_return_row(row)
            is_buyout = _is_sales_buyout_row(row)
            if not is_return and not is_buyout:
                continue

            buyout_dt = _normalize_sales_cursor(row.get("date")) or _normalize_sales_cursor(row.get("lastChangeDate"))
            if is_return:
                updated = Order.objects.filter(seller=seller, srid=srid).update(
                    is_return=True,
                    is_buyout=False,
                    buyout_date=None,
                )
                return_marks += int(updated > 0)
            else:
                updated = Order.objects.filter(seller=seller, srid=srid).update(
                    is_buyout=True,
                    is_return=False,
                    buyout_date=buyout_dt,
                )
                buyout_marks += int(updated > 0)

        next_cursor = latest_change_dt
        if len(rows) < 80000:
            break
        # Требование WB по лимиту: 1 запрос в минуту
        time.sleep(60.5)

    meta["sales_sync"] = {
        "last_change_date": _format_sales_cursor(next_cursor),
        "updated_at": timezone.localtime().isoformat(),
        "rows_last_run": total_rows,
        "bootstrap_completed": True,
    }
    seller.sync_meta = meta
    seller.save(update_fields=["sync_meta"])

    return {
        "rows": total_rows,
        "pages": pages,
        "buyout_marks": buyout_marks,
        "return_marks": return_marks,
        "matched_srids": len(processed_srids),
    }
