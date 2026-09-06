from __future__ import annotations

import calendar
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

TIER_LABELS = {
    UserSubscription.TIER_READ: "Чтение",
    UserSubscription.TIER_READ_WRITE: "Чтение + запись",
}

TIER_READ_PRICES = {
    UserSubscription.PLAN_MONTH_1: 1990,
    UserSubscription.PLAN_MONTH_6: 9990,
    UserSubscription.PLAN_MONTH_12: 17990,
}

TIER_READ_WRITE_PRICES = {
    UserSubscription.PLAN_MONTH_1: 2985,
    UserSubscription.PLAN_MONTH_6: 14985,
    UserSubscription.PLAN_MONTH_12: 26985,
}

TIER_PRICE_TABLES = {
    UserSubscription.TIER_READ: TIER_READ_PRICES,
    UserSubscription.TIER_READ_WRITE: TIER_READ_WRITE_PRICES,
}

ALL_TIER_CODES = (UserSubscription.TIER_READ, UserSubscription.TIER_READ_WRITE)

# Backward-compatible alias for legacy imports
PLAN_PRICES = TIER_READ_PRICES


def _add_calendar_months(dt_value, months: int):
    if dt_value is None:
        dt_value = timezone.now()
    months = max(0, int(months or 0))
    month_index = dt_value.month - 1 + months
    year = dt_value.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt_value.day, last_day)
    return dt_value.replace(year=year, month=month, day=day)


def get_plan_price(tier_code: str, plan_code: str) -> int:
    table = TIER_PRICE_TABLES.get(tier_code)
    if not table:
        raise ValueError("Unknown subscription tier")
    price = table.get(plan_code)
    if price is None:
        raise ValueError("Unknown subscription plan")
    return int(price)


def get_or_create_subscription(user) -> UserSubscription:
    now_dt = timezone.now()
    sub, created = UserSubscription.objects.get_or_create(
        user=user,
        defaults={
            "tier_code": UserSubscription.TIER_READ,
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


def has_read_access(user, sub: UserSubscription | None) -> bool:
    if user and getattr(user, "is_superuser", False):
        return True
    return has_active_access(sub)


def has_write_access(user, sub: UserSubscription | None) -> bool:
    if user and getattr(user, "is_superuser", False):
        return True
    if not has_active_access(sub):
        return False
    return sub.tier_code == UserSubscription.TIER_READ_WRITE


def extend_subscription_access(
    sub: UserSubscription,
    *,
    plan_code: str,
    tier_code: str | None = None,
    now_dt=None,
) -> UserSubscription:
    now_dt = now_dt or timezone.now()
    months = PLAN_MONTHS.get(plan_code)
    if not months:
        raise ValueError("Unknown subscription plan")
    tier = tier_code or sub.tier_code or UserSubscription.TIER_READ
    if tier not in TIER_PRICE_TABLES:
        raise ValueError("Unknown subscription tier")

    sub = normalize_subscription_status(sub)
    current_expires = sub.access_expires_at
    base_start = now_dt
    if current_expires and current_expires > now_dt:
        base_start = current_expires

    sub.tier_code = tier
    sub.plan_code = plan_code
    sub.status = UserSubscription.STATUS_ACTIVE
    sub.paid_from = base_start
    sub.paid_to = _add_calendar_months(base_start, months)
    sub.access_expires_at = sub.paid_to
    sub.save(
        update_fields=[
            "tier_code",
            "plan_code",
            "status",
            "paid_from",
            "paid_to",
            "access_expires_at",
            "updated_at",
        ]
    )
    return sub


def build_subscription_summary(sub: UserSubscription | None, *, user=None) -> dict:
    if not sub:
        return {
            "status": "none",
            "status_label": "Нет подписки",
            "tier_code": UserSubscription.TIER_READ,
            "tier_label": TIER_LABELS.get(UserSubscription.TIER_READ, "Чтение"),
            "plan_code": UserSubscription.PLAN_MONTH_1,
            "plan_label": PLAN_LABELS.get(UserSubscription.PLAN_MONTH_1, "1 месяц"),
            "access_expires_at": None,
            "trial_ends_at": None,
            "days_left": 0,
            "has_access": False,
            "has_read_access": False,
            "has_write_access": False,
        }
    sub = normalize_subscription_status(sub)
    now_dt = timezone.now()
    expires = sub.access_expires_at
    days_left = 0
    if expires:
        days_left = max(0, (expires.date() - now_dt.date()).days)
    status_label_map = {
        UserSubscription.STATUS_TRIAL: "Бесплатный доступ (чтение)",
        UserSubscription.STATUS_ACTIVE: "Активна",
        UserSubscription.STATUS_PAST_DUE: "Ожидает оплату",
        UserSubscription.STATUS_EXPIRED: "Истекла",
        UserSubscription.STATUS_CANCELED: "Отменена",
    }
    return {
        "status": sub.status,
        "status_label": status_label_map.get(sub.status, sub.status),
        "tier_code": sub.tier_code,
        "tier_label": TIER_LABELS.get(sub.tier_code, sub.tier_code),
        "plan_code": sub.plan_code,
        "plan_label": PLAN_LABELS.get(sub.plan_code, sub.plan_code),
        "access_expires_at": sub.access_expires_at,
        "trial_ends_at": sub.trial_ends_at,
        "days_left": days_left,
        "has_access": has_active_access(sub),
        "has_read_access": has_read_access(user, sub),
        "has_write_access": has_write_access(user, sub),
    }


def _pricing_cards_for_tier(tier_code: str) -> list[dict]:
    table = TIER_PRICE_TABLES[tier_code]
    cards = []
    for code in (UserSubscription.PLAN_MONTH_1, UserSubscription.PLAN_MONTH_6, UserSubscription.PLAN_MONTH_12):
        months = PLAN_MONTHS.get(code, 1)
        price_total = int(table.get(code, 0))
        monthly = int(round(price_total / max(1, months)))
        cards.append(
            {
                "tier_code": tier_code,
                "tier_label": TIER_LABELS.get(tier_code, tier_code),
                "code": code,
                "months": months,
                "label": PLAN_LABELS.get(code, code),
                "price_total": price_total,
                "price_monthly": monthly,
            }
        )
    return cards


def pricing_sections() -> list[dict]:
    return [
        {
            "tier_code": UserSubscription.TIER_READ,
            "tier_label": TIER_LABELS[UserSubscription.TIER_READ],
            "description": "Синхронизация и аналитика по данным WB (только чтение).",
            "plans": _pricing_cards_for_tier(UserSubscription.TIER_READ),
        },
        {
            "tier_code": UserSubscription.TIER_READ_WRITE,
            "tier_label": TIER_LABELS[UserSubscription.TIER_READ_WRITE],
            "description": "Всё из «Чтение» плюс отправка изменений в WB через API.",
            "plans": _pricing_cards_for_tier(UserSubscription.TIER_READ_WRITE),
        },
    ]


def pricing_cards() -> list[dict]:
    """Flat list of all tier+plan cards (legacy helper)."""
    out: list[dict] = []
    for section in pricing_sections():
        out.extend(section["plans"])
    return out
