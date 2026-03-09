from datetime import timedelta
from django.utils import timezone
from django.db.models import Count
from core.models import Order


def get_sales_last_14_days(seller):
    date_from = timezone.now() - timedelta(days=14)

    # Для прогноза спроса учитываем все заказы по региону назначения,
    # а не только локальные отгрузки.
    sales = (
        Order.objects
        .filter(
            seller=seller,
            order_date__gte=date_from,
            warehouse_type="Склад WB",
            is_cancel=False,
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

def calculate_replenishment(seller, safety_coef=1.0):
    """
    safety_coef = 1.15 если хочешь +15% страхового запаса
    """

    forecast = build_month_forecast(seller)
    current_stock = get_current_stock_by_region(seller)
    print(f"Прогнозируемые продажи за 30 дней: {len(forecast)} позиций")
    print(f"Текущий складской запас: {len(current_stock)} позиций")

    result = []

    all_keys = set(forecast.keys()) | set(current_stock.keys())
    print(f"Всего уникальных (nm_id, supplier_article, region) в прогнозе и стоке: {len(all_keys)}")

    for key in all_keys:
        nm_id, supplier_article, region = key

        needed = forecast.get(key, 0)
        needed = round(needed * safety_coef)

        stock = current_stock.get(key, 0)

        to_ship = max(needed - stock, 0)

        result.append({
            "nm_id": nm_id,
            "supplier_article": supplier_article,
            "region": region,
            "forecast_30_days": needed,
            "current_stock": stock,
            "to_ship": to_ship,
        })

    # Можно оставить только то, что реально нужно везти:
    result = [r for r in result if r["to_ship"] > 0]

    return result
