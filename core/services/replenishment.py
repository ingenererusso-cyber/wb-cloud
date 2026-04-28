from datetime import timedelta
from django.utils import timezone
from django.db.models import Count
from app.services.supply_recommendations.loaders import load_positive_fbs_stock_keys
from django.db.models import Sum
from core.models import Order, ProductCardSize, SellerFbsStock


def get_sales_last_14_days(seller):
    date_from = timezone.now() - timedelta(days=14)

    # Для прогноза спроса учитываем все заказы по региону назначения,
    # а не только локальные отгрузки.
    sales = (
        Order.objects
        .filter(
            seller=seller,
            order_date__gte=date_from,
            is_cancel=False,
            is_return=False,
        )
        .exclude(oblast_okrug_name__isnull=True)
        .exclude(oblast_okrug_name="")
        .values("nm_id", "supplier_article", "oblast_okrug_name")
        .annotate(qty=Count("id"))
    )

    return sales

def build_month_forecast(seller):
    sales = get_sales_last_14_days(seller)

    forecast = {}

    for row in sales:
        nm_id = row["nm_id"]
        supplier_article = (row["supplier_article"] or "").strip()
        region = normalize_district(row["oblast_okrug_name"])
        qty_14 = row["qty"]

        if not region:
            continue

        daily_speed = qty_14 / 14
        forecast_30 = round(daily_speed * 30)

        forecast[(nm_id, supplier_article, region)] = forecast_30

    return forecast

from core.models import WarehouseStockDetailed
from core.services.localization import normalize_district, find_office


def get_current_stock_by_region(seller):
    stock_map = {}

    stocks = WarehouseStockDetailed.objects.filter(seller=seller)

    for s in stocks:
        office = find_office(s.warehouse_name)
        if not office:
            continue

        region = normalize_district(office.federal_district)
        if not region:
            continue

        supplier_article = (s.supplier_article or "").strip()
        key = (s.nm_id, supplier_article, region)

        stock_map[key] = stock_map.get(key, 0) + s.quantity

    return stock_map


def get_total_fbs_stock_by_product(seller):
    stock_map = {}
    chrt_to_meta = {
        int(row["chrt_id"]): (
            row.get("nm_id"),
            (row.get("vendor_code") or "").strip(),
        )
        for row in (
            ProductCardSize.objects
            .filter(seller=seller)
            .exclude(chrt_id__isnull=True)
            .values("chrt_id", "nm_id", "vendor_code")
        )
        if row.get("chrt_id") is not None
    }

    rows = (
        SellerFbsStock.objects
        .filter(seller=seller, amount__gt=0)
        .values("chrt_id")
        .annotate(total_amount=Sum("amount"))
    )

    for row in rows:
        chrt_id = row.get("chrt_id")
        if chrt_id is None:
            continue
        meta = chrt_to_meta.get(int(chrt_id))
        if not meta:
            continue
        nm_id, supplier_article = meta
        if nm_id is None:
            continue
        key = (nm_id, supplier_article)
        stock_map[key] = stock_map.get(key, 0) + int(row.get("total_amount") or 0)

    return stock_map

def _filter_rows_by_fbs_stock(items_map, positive_nm_ids, positive_supplier_articles):
    if not positive_nm_ids and not positive_supplier_articles:
        return {}

    filtered = {}
    for key, value in items_map.items():
        nm_id, supplier_article, _region = key
        normalized_supplier_article = (supplier_article or "").strip()
        has_fbs_stock = (
            nm_id in positive_nm_ids
            or (normalized_supplier_article and normalized_supplier_article in positive_supplier_articles)
        )
        if has_fbs_stock:
            filtered[key] = value
    return filtered


def calculate_replenishment(seller, safety_coef=1.0, only_with_fbs_stock=False):
    """
    safety_coef = 1.15 если хочешь +15% страхового запаса
    """

    forecast = build_month_forecast(seller)
    current_stock = get_current_stock_by_region(seller)
    total_fbs_stock = get_total_fbs_stock_by_product(seller)

    if only_with_fbs_stock:
        positive_nm_ids, positive_supplier_articles = load_positive_fbs_stock_keys(seller=seller)
        forecast = _filter_rows_by_fbs_stock(forecast, positive_nm_ids, positive_supplier_articles)
        current_stock = _filter_rows_by_fbs_stock(current_stock, positive_nm_ids, positive_supplier_articles)
        total_fbs_stock = {
            key: value
            for key, value in total_fbs_stock.items()
            if key[0] in positive_nm_ids or (key[1] and key[1] in positive_supplier_articles)
        }

    result = []

    all_keys = set(forecast.keys()) | set(current_stock.keys())

    for key in all_keys:
        nm_id, supplier_article, region = key

        needed = forecast.get(key, 0)
        needed = round(needed * safety_coef)

        stock = current_stock.get(key, 0)
        fbs_stock = total_fbs_stock.get((nm_id, supplier_article), 0)

        to_ship = max(needed - stock, 0)

        result.append({
            "nm_id": nm_id,
            "supplier_article": supplier_article,
            "region": region,
            "forecast_30_days": needed,
            "current_stock": stock,
            "current_fbs_stock": fbs_stock,
            "to_ship": to_ship,
        })

    # Можно оставить только то, что реально нужно везти:
    result = [r for r in result if r["to_ship"] > 0]

    return result
