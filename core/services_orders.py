from datetime import datetime, timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from wb_api.client import WBOrdersSupplierClient
from .models import Order, SellerAccount
from core.services.localization import determine_locality


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


def sync_fbw_orders(seller: SellerAccount, days_back: int = 90):
    """
    Загружает заказы за последние days_back дней.
    """

    client = WBOrdersSupplierClient(seller.api_token)
    default_tz = timezone.get_default_timezone()

    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    rows = client.get_orders(date_from=date_from)

    print(f"Получено заказов: {len(rows)}")

    for r in rows:
        is_fbw = r.get("warehouseType") == "Склад WB"
        is_local = determine_locality(r["warehouseName"], r.get("oblastOkrugName")) if is_fbw else False
        order_date = _to_aware_datetime(r.get("date"), default_tz)
        last_change_date = _to_aware_datetime(r.get("lastChangeDate"), default_tz)

        Order.objects.update_or_create(
            srid=r["srid"],  # уникальный ID заказа
            defaults={
                "seller": seller,
                "nm_id": r["nmId"],
                "supplier_article": r["supplierArticle"],
                "tech_size": r["techSize"],
                "warehouse_name": r["warehouseName"],
                "warehouse_type": r["warehouseType"],
                "region_name": r.get("regionName"),
                "country_name": r.get("countryName"),
                "oblast_okrug_name": r.get("oblastOkrugName"),
                "is_cancel": r["isCancel"],
                "finished_price": r.get("finishedPrice"),
                "order_date": order_date,
                "last_change_date": last_change_date,
                "is_local": is_local,
            }
        )
