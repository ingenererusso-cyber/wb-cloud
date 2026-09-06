from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import PromoCode, PromoRedemption, UserSubscription
from core.subscriptions import (
    ALL_TIER_CODES,
    PLAN_MONTHS,
    TIER_LABELS,
    TIER_PRICE_TABLES,
    get_or_create_subscription,
    has_active_access,
    normalize_subscription_status,
)


def normalize_promo_code(raw: str) -> str:
    return (raw or "").strip().upper()


def _parse_valid_until(raw) -> datetime | None:
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    dt = parse_datetime(s)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@dataclass
class PromoValidationError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover - dataclass default
        return self.message


def _valid_plan_codes() -> set[str]:
    return {UserSubscription.PLAN_MONTH_1, UserSubscription.PLAN_MONTH_6, UserSubscription.PLAN_MONTH_12}


def _valid_tier_codes() -> set[str]:
    return set(ALL_TIER_CODES)


def promo_tier_scope(promo: PromoCode) -> set[str]:
    raw = promo.applies_to_tier_codes or []
    if not raw:
        return _valid_tier_codes()
    return set(raw)


def tier_scope_label(promo: PromoCode) -> str:
    scope = promo_tier_scope(promo)
    if scope == _valid_tier_codes():
        return "Оба"
    labels = []
    if UserSubscription.TIER_READ in scope:
        labels.append("Чтение")
    if UserSubscription.TIER_READ_WRITE in scope:
        labels.append("Запись")
    return " + ".join(labels) if labels else "Оба"


def free_days_tier_grant(promo: PromoCode) -> str:
    scope = promo_tier_scope(promo)
    if scope == {UserSubscription.TIER_READ}:
        return UserSubscription.TIER_READ
    return UserSubscription.TIER_READ_WRITE


def resolve_tier_after_free_days(sub: UserSubscription, promo: PromoCode) -> str:
    granted = free_days_tier_grant(promo)
    if sub.tier_code == UserSubscription.TIER_READ_WRITE and granted == UserSubscription.TIER_READ:
        return UserSubscription.TIER_READ_WRITE
    return granted


def get_promo_by_code(code: str) -> PromoCode | None:
    norm = normalize_promo_code(code)
    if not norm:
        return None
    return PromoCode.objects.filter(code=norm).first()


def redemption_count(promo: PromoCode) -> int:
    return PromoRedemption.objects.filter(promo=promo).count()


def user_has_redeemed(promo: PromoCode, user) -> bool:
    return PromoRedemption.objects.filter(promo=promo, user=user).exists()


def assert_promo_usable(
    promo: PromoCode,
    *,
    user,
    plan_code: str | None = None,
    tier_code: str | None = None,
) -> None:
    """Raises PromoValidationError if promo cannot be used."""
    now = timezone.now()
    if not promo.is_active:
        raise PromoValidationError("Промокод неактивен.")
    if promo.valid_until < now:
        raise PromoValidationError("Срок действия промокода истёк.")
    if promo.max_uses is not None and redemption_count(promo) >= promo.max_uses:
        raise PromoValidationError("Лимит использований промокода исчерпан.")
    if user_has_redeemed(promo, user):
        raise PromoValidationError("Этот промокод уже использован вами.")

    if promo.kind == PromoCode.KIND_PRICE_DISCOUNT:
        pct = promo.discount_percent
        if pct is None or not (1 <= int(pct) <= 100):
            raise PromoValidationError("Промокод настроен некорректно (скидка).")
        plans = promo.applies_to_plan_codes or []
        if not isinstance(plans, list) or not plans:
            raise PromoValidationError("Промокод настроен некорректно (периоды).")
        valid = _valid_plan_codes()
        if not set(plans).issubset(valid):
            raise PromoValidationError("Промокод настроен некорректно (неизвестный период).")
        if plan_code and plan_code not in plans:
            raise PromoValidationError("Промокод не распространяется на выбранный период.")
        if tier_code and tier_code not in promo_tier_scope(promo):
            raise PromoValidationError("Промокод не распространяется на выбранный блок.")
    elif promo.kind == PromoCode.KIND_FREE_DAYS:
        days = promo.free_days
        if days is None or int(days) < 1:
            raise PromoValidationError("Промокод настроен некорректно (дни).")
    else:
        raise PromoValidationError("Неизвестный тип промокода.")


def discounted_price_total(base_total: int, discount_percent: int) -> int:
    pct = max(1, min(100, int(discount_percent)))
    raw = int(round(base_total * (100 - pct) / 100.0))
    return max(0, raw)


def build_discounted_prices(promo: PromoCode) -> dict[str, dict[str, dict[str, int]]]:
    """Nested prices by tier and plan; discount only where tier+plan match promo scope."""
    assert promo.kind == PromoCode.KIND_PRICE_DISCOUNT
    pct = int(promo.discount_percent or 0)
    applies_plans = set(promo.applies_to_plan_codes or [])
    applies_tiers = promo_tier_scope(promo)
    out: dict[str, dict[str, dict[str, int]]] = {}
    for tier_code, table in TIER_PRICE_TABLES.items():
        tier_prices: dict[str, dict[str, int]] = {}
        for plan_code, base in table.items():
            months = PLAN_MONTHS.get(plan_code, 1)
            if plan_code in applies_plans and tier_code in applies_tiers:
                total = discounted_price_total(int(base), pct)
            else:
                total = int(base)
            monthly = int(round(total / max(1, months)))
            tier_prices[plan_code] = {"price_total": total, "price_monthly": monthly}
        out[tier_code] = tier_prices
    return out


def preview_promo(
    *,
    user,
    code: str,
    plan_code: str | None = None,
    tier_code: str | None = None,
) -> dict[str, Any]:
    """Read-only preview; no DB writes."""
    norm = normalize_promo_code(code)
    if not norm:
        raise PromoValidationError("Введите промокод.")
    promo = get_promo_by_code(norm)
    if not promo:
        raise PromoValidationError("Промокод не найден.")
    assert_promo_usable(
        promo,
        user=user,
        plan_code=plan_code if promo.kind == PromoCode.KIND_PRICE_DISCOUNT else None,
        tier_code=tier_code if promo.kind == PromoCode.KIND_PRICE_DISCOUNT else None,
    )
    applies_tiers = sorted(promo_tier_scope(promo))
    if promo.kind == PromoCode.KIND_PRICE_DISCOUNT:
        return {
            "ok": True,
            "kind": promo.kind,
            "title": promo.title,
            "discount_percent": promo.discount_percent,
            "applies_to_tiers": applies_tiers,
            "tier_scope_label": tier_scope_label(promo),
            "prices": build_discounted_prices(promo),
        }
    days = int(promo.free_days or 0)
    granted_tier = free_days_tier_grant(promo)
    tier_label = TIER_LABELS.get(granted_tier, granted_tier)
    return {
        "ok": True,
        "kind": promo.kind,
        "title": promo.title,
        "free_days": days,
        "applies_to_tiers": applies_tiers,
        "tier_scope_label": tier_scope_label(promo),
        "tier_grant": granted_tier,
        "tier_grant_label": tier_label,
        "message": f"Промокод добавит {days} дн. · блок: {tier_label}.",
    }


@transaction.atomic
def apply_free_days_promo(*, user, code: str) -> dict[str, Any]:
    norm = normalize_promo_code(code)
    if not norm:
        raise PromoValidationError("Введите промокод.")
    promo = PromoCode.objects.select_for_update().filter(code=norm).first()
    if not promo:
        raise PromoValidationError("Промокод не найден.")
    if promo.kind != PromoCode.KIND_FREE_DAYS:
        raise PromoValidationError("Этот промокод не даёт бесплатные дни.")
    assert_promo_usable(promo, user=user)
    n = int(promo.free_days or 0)
    if n < 1:
        raise PromoValidationError("Промокод настроен некорректно (дни).")

    sub = get_or_create_subscription(user)
    sub = UserSubscription.objects.select_for_update().get(pk=sub.pk)
    sub = normalize_subscription_status(sub)
    now = timezone.now()
    delta = timedelta(days=n)
    expires = sub.access_expires_at
    new_tier = resolve_tier_after_free_days(sub, promo)

    if has_active_access(sub) or (expires and expires > now):
        base = expires or now
        if base < now:
            base = now
        sub.access_expires_at = base + delta
        if sub.status in {
            UserSubscription.STATUS_EXPIRED,
            UserSubscription.STATUS_CANCELED,
            UserSubscription.STATUS_PAST_DUE,
        }:
            sub.status = UserSubscription.STATUS_ACTIVE
    else:
        sub.access_expires_at = now + delta
        if sub.status in {
            UserSubscription.STATUS_EXPIRED,
            UserSubscription.STATUS_CANCELED,
            UserSubscription.STATUS_PAST_DUE,
        }:
            sub.status = UserSubscription.STATUS_ACTIVE

    sub.tier_code = new_tier
    sub.save(
        update_fields=[
            "access_expires_at",
            "status",
            "tier_code",
            "updated_at",
        ]
    )
    PromoRedemption.objects.create(
        promo=promo,
        user=user,
        plan_code="",
        extra={
            "kind": promo.kind,
            "free_days": n,
            "tier_code": new_tier,
        },
    )
    return {
        "ok": True,
        "free_days": n,
        "tier_code": new_tier,
        "tier_label": TIER_LABELS.get(new_tier, new_tier),
        "access_expires_at": sub.access_expires_at.isoformat() if sub.access_expires_at else None,
    }


@transaction.atomic
def redeem_price_discount_on_checkout(
    *,
    user,
    code: str,
    plan_code: str,
    tier_code: str,
) -> PromoCode:
    norm = normalize_promo_code(code)
    if not norm:
        raise PromoValidationError("Промокод не указан.")
    promo = PromoCode.objects.select_for_update().filter(code=norm).first()
    if not promo:
        raise PromoValidationError("Промокод не найден.")
    if promo.kind != PromoCode.KIND_PRICE_DISCOUNT:
        raise PromoValidationError("Этот промокод не даёт скидку на тариф.")
    assert_promo_usable(promo, user=user, plan_code=plan_code, tier_code=tier_code)
    PromoRedemption.objects.create(
        promo=promo,
        user=user,
        plan_code=plan_code,
        extra={
            "kind": promo.kind,
            "discount_percent": promo.discount_percent,
            "tier_code": tier_code,
        },
    )
    return promo


def annotate_promo_list(qs):
    return qs.annotate(uses_count=Count("redemptions"))


def _parse_tier_codes_from_data(data: dict[str, Any]) -> list[str]:
    raw = data.get("applies_to_tier_codes")
    if isinstance(raw, str):
        items = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, list):
        items = [str(p).strip() for p in raw if str(p).strip()]
    else:
        items = []
    valid = _valid_tier_codes()
    return [p for p in items if p in valid]


def create_promo_from_post(data: dict[str, Any]) -> PromoCode | list[str]:
    """Returns PromoCode or list of error strings."""
    errors: list[str] = []
    kind = (data.get("kind") or "").strip()
    code = normalize_promo_code(data.get("code") or "")
    title = (data.get("title") or "").strip()
    if not code:
        errors.append("Укажите код промокода.")
    if len(title) < 2:
        errors.append("Укажите наименование (от 2 символов).")
    valid_until_raw = data.get("valid_until")
    if not valid_until_raw:
        errors.append("Укажите срок действия.")
    max_uses_raw = data.get("max_uses")
    max_uses: int | None
    if max_uses_raw in (None, "",):
        max_uses = None
    else:
        try:
            max_uses = max(1, int(max_uses_raw))
        except (TypeError, ValueError):
            errors.append("Некорректный лимит использований.")
            max_uses = None

    if kind not in {PromoCode.KIND_PRICE_DISCOUNT, PromoCode.KIND_FREE_DAYS}:
        errors.append("Некорректный тип промокода.")

    if errors:
        return errors

    assert max_uses is None or max_uses >= 1

    valid_until = _parse_valid_until(valid_until_raw)
    if valid_until is None:
        return ["Некорректная дата срока действия."]

    discount_percent = None
    applies_to: list[str] = []
    applies_to_tiers: list[str] = _parse_tier_codes_from_data(data)
    free_days = None
    if kind == PromoCode.KIND_PRICE_DISCOUNT:
        try:
            discount_percent = int(data.get("discount_percent") or 0)
        except (TypeError, ValueError):
            discount_percent = 0
        if not (1 <= discount_percent <= 100):
            errors.append("Скидка должна быть от 1 до 100%.")
        raw_plans = data.get("applies_to_plan_codes")
        if isinstance(raw_plans, str):
            applies_to = [p.strip() for p in raw_plans.split(",") if p.strip()]
        elif isinstance(raw_plans, list):
            applies_to = [str(p).strip() for p in raw_plans if str(p).strip()]
        else:
            applies_to = []
        valid = _valid_plan_codes()
        applies_to = [p for p in applies_to if p in valid]
        if not applies_to:
            errors.append("Выберите хотя бы один период тарифа для скидки.")
    else:
        try:
            free_days = int(data.get("free_days") or 0)
        except (TypeError, ValueError):
            free_days = 0
        if free_days < 1:
            errors.append("Укажите количество бесплатных дней (≥ 1).")

    if errors:
        return errors

    if PromoCode.objects.filter(code=code).exists():
        return ["Промокод с таким кодом уже существует."]

    promo = PromoCode(
        code=code,
        title=title,
        kind=kind,
        valid_until=valid_until,
        max_uses=max_uses,
        is_active=str(data.get("is_active", "1")).lower() in {"1", "true", "yes", "on"},
        discount_percent=discount_percent,
        applies_to_plan_codes=applies_to if kind == PromoCode.KIND_PRICE_DISCOUNT else [],
        applies_to_tier_codes=applies_to_tiers,
        free_days=free_days,
    )
    promo.save()
    return promo
