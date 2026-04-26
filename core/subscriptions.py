from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.models import UserSubscription


TRIAL_DAYS = 3

PLAN_MONTHS = {
    UserSubscription.PLAN_MONTH_1: 1,
    UserSubscription.PLAN_MONTH_6: 6,
    UserSubscription.PLAN_MONTH_12: 12,
}

PLAN_LABELS = {
    UserSubscription.PLAN_MONTH_1: "1 месяц",
    UserSubscription.PLAN_MONTH_6: "6 месяцев",
    UserSubscription.PLAN_MONTH_12: "12 месяцев",
}

PLAN_PRICES = {
    UserSubscription.PLAN_MONTH_1: 1990,
    UserSubscription.PLAN_MONTH_6: 9990,
    UserSubscription.PLAN_MONTH_12: 17990,
}


def get_or_create_subscription(user) -> UserSubscription:
    now_dt = timezone.now()
    sub, created = UserSubscription.objects.get_or_create(
        user=user,
        defaults={
            "plan_code": UserSubscription.PLAN_MONTH_1,
            "status": UserSubscription.STATUS_TRIAL,
            "trial_started_at": now_dt,
            "trial_ends_at": now_dt + timedelta(days=TRIAL_DAYS),
            "access_expires_at": now_dt + timedelta(days=TRIAL_DAYS),
        },
    )
    if created:
        return sub
    return normalize_subscription_status(sub)


def normalize_subscription_status(sub: UserSubscription) -> UserSubscription:
    now_dt = timezone.now()
    access_expires = sub.access_expires_at
    if access_expires and access_expires < now_dt and sub.status in {
        UserSubscription.STATUS_TRIAL,
        UserSubscription.STATUS_ACTIVE,
        UserSubscription.STATUS_PAST_DUE,
    }:
        sub.status = UserSubscription.STATUS_EXPIRED
        sub.save(update_fields=["status", "updated_at"])
    return sub


def has_active_access(sub: UserSubscription | None) -> bool:
    if not sub:
        return False
    sub = normalize_subscription_status(sub)
    if sub.status not in {UserSubscription.STATUS_TRIAL, UserSubscription.STATUS_ACTIVE}:
        return False
    if sub.access_expires_at and sub.access_expires_at < timezone.now():
        return False
    return True


def build_subscription_summary(sub: UserSubscription | None) -> dict:
    if not sub:
        return {
            "status": "none",
            "status_label": "Нет подписки",
            "plan_code": UserSubscription.PLAN_MONTH_1,
            "plan_label": PLAN_LABELS.get(UserSubscription.PLAN_MONTH_1, "1 месяц"),
            "access_expires_at": None,
            "trial_ends_at": None,
            "days_left": 0,
            "has_access": False,
        }
    sub = normalize_subscription_status(sub)
    now_dt = timezone.now()
    expires = sub.access_expires_at
    days_left = 0
    if expires:
        days_left = max(0, (expires.date() - now_dt.date()).days)
    status_label_map = {
        UserSubscription.STATUS_TRIAL: "Бесплатный полный доступ",
        UserSubscription.STATUS_ACTIVE: "Активна",
        UserSubscription.STATUS_PAST_DUE: "Ожидает оплату",
        UserSubscription.STATUS_EXPIRED: "Истекла",
        UserSubscription.STATUS_CANCELED: "Отменена",
    }
    return {
        "status": sub.status,
        "status_label": status_label_map.get(sub.status, sub.status),
        "plan_code": sub.plan_code,
        "plan_label": PLAN_LABELS.get(sub.plan_code, sub.plan_code),
        "access_expires_at": sub.access_expires_at,
        "trial_ends_at": sub.trial_ends_at,
        "days_left": days_left,
        "has_access": has_active_access(sub),
    }


def pricing_cards() -> list[dict]:
    cards = []
    for code in (UserSubscription.PLAN_MONTH_1, UserSubscription.PLAN_MONTH_6, UserSubscription.PLAN_MONTH_12):
        months = PLAN_MONTHS.get(code, 1)
        price_total = int(PLAN_PRICES.get(code, 0))
        monthly = int(round(price_total / max(1, months)))
        cards.append(
            {
                "code": code,
                "months": months,
                "label": PLAN_LABELS.get(code, code),
                "price_total": price_total,
                "price_monthly": monthly,
            }
        )
    return cards
