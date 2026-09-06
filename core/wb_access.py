from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from core.subscriptions import get_or_create_subscription, has_write_access

WRITE_ACTIONS = {
    "fbs_stocks.update": {
        "label": "Отправка FBS-остатков",
        "view_action": "update_fbs_stocks",
    },
}


def subscription_write_access(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    sub = get_or_create_subscription(user)
    return has_write_access(user, sub)


def deny_write_access_message() -> str:
    return (
        "Отправка изменений в WB доступна в тарифе «Чтение + запись». "
        f"Перейдите на страницу тарифов: {reverse('pricing_page')}"
    )


def require_write_access_html(request, *, redirect_name: str = "fbs_stocks_report"):
    if subscription_write_access(request):
        return None
    messages.error(request, deny_write_access_message())
    return redirect(redirect_name)


def require_write_access_json(request):
    if subscription_write_access(request):
        return None
    return JsonResponse(
        {
            "ok": False,
            "error": "write_access_denied",
            "message": deny_write_access_message(),
        },
        status=403,
    )
