from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from core.models import SellerAccount
from core.services.replenishment import calculate_replenishment
from core.services_offices import sync_wb_offices
from core.services_orders import sync_fbw_orders
from core.services.localization import (
    get_local_orders_percent_last_full_week,
    get_local_orders_percent_trend_last_full_weeks,
)


def _get_seller_for_user(user):
    try:
        return user.seller_account
    except SellerAccount.DoesNotExist:
        return None


def _get_or_create_seller_for_user(user):
    seller = _get_seller_for_user(user)
    if seller:
        return seller
    display_name = user.get_full_name().strip() or user.username
    seller, _ = SellerAccount.objects.get_or_create(
        user=user,
        defaults={"name": display_name, "api_token": ""},
    )
    return seller


@login_required
def home(request):
    seller = _get_or_create_seller_for_user(request.user)

    if request.method == "POST" and request.POST.get("action") == "sync_orders":
        api_token = (seller.api_token or "").strip()
        if not api_token:
            messages.error(request, "Сначала добавьте API-ключ в настройках аккаунта.")
            return redirect("home")
        try:
            offices_count = sync_wb_offices(seller)
            orders_count = sync_fbw_orders(seller, days_back=175)
            messages.success(
                request,
                f"Синхронизация завершена: складов {offices_count}, заказов {orders_count}.",
            )
        except Exception as exc:
            messages.error(request, f"Ошибка синхронизации: {exc}")
        return redirect("home")

    local_orders_percent = None
    local_orders_trend = {"points": []}
    missing_api_token = not seller or not (seller.api_token or "").strip()

    if seller:
        local_orders_percent = get_local_orders_percent_last_full_week(seller)
        local_orders_trend = get_local_orders_percent_trend_last_full_weeks(seller, weeks=25)

    return render(
        request,
        "home.html",
        {
            "local_orders_percent": local_orders_percent,
            "local_orders_trend": local_orders_trend,
            "seller": seller,
            "missing_api_token": missing_api_token,
        },
    )


@login_required
def replenishment_report(request):
    seller = _get_seller_for_user(request.user)
    data = calculate_replenishment(seller) if seller else []

    return render(
        request,
        "replenishment/report.html",
        {"rows": data, "seller": seller}
    )


@login_required
def account_settings(request):
    seller = _get_or_create_seller_for_user(request.user)

    if request.method == "POST":
        seller.api_token = request.POST.get("api_token", "").strip()
        seller.save(update_fields=["api_token"])
        return redirect(f"{reverse('account_settings')}?saved=1")

    return render(
        request,
        "account/settings.html",
        {
            "seller": seller,
            "saved": request.GET.get("saved") == "1",
        },
    )
