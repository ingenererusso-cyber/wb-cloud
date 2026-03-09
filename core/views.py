from django.shortcuts import render
from core.services.replenishment import calculate_replenishment
from core.services.localization import (
    get_local_orders_percent_last_full_week,
    get_local_orders_percent_trend_last_full_weeks,
)
from core.models import SellerAccount


def home(request):
    seller = SellerAccount.objects.first()
    local_orders_percent = None
    local_orders_trend = {"points": []}

    if seller:
        local_orders_percent = get_local_orders_percent_last_full_week(seller)
        local_orders_trend = get_local_orders_percent_trend_last_full_weeks(seller, weeks=25)

    return render(
        request,
        "home.html",
        {
            "local_orders_percent": local_orders_percent,
            "local_orders_trend": local_orders_trend,
        },
    )


def replenishment_report(request):

    seller = SellerAccount.objects.first()

    data = calculate_replenishment(seller)
    print(data[:5])  # печатаем первые 5 строк для проверки

    return render(
        request,
        "replenishment/report.html",
        {"rows": data}
    )
