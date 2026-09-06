"""
Ценовой робот: динамическое ценообразование под ликвидацию сезонного запаса.

Задача (см. PRICE_ROBOT.md): распродать весь склад игрушек в 0 к дедлайну сезона
(≈15 фев, hard 1 мар — обязательная маркировка) при максимальной суммарной выручке.

Ядро — контроллер целевой скорости продаж (target sell-through) с учётом сезонной
кривой спроса, откалиброванной по истории (декабрь ≈ половина сезона; инеластичный
пик 15–26 дек; обвал после 26 дек). Сигнал управления — ЗАКАЗЫ (не выкупы: те
приходят с лагом 1–2 недели); выкупаемость применяется только к проекции остатка.

Робот работает по nm_id (у продавца нет размеров). Цена в WB задаётся как
base price + discount(%); покупатель видит net = price * (1 - discount/100).
Чтобы поднять net — уменьшаем discount; чтобы снизить — увеличиваем.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    Order,
    PricingPolicy,
    Product,
    ProductCardSize,
    ProductSizePrice,
    SellerAccount,
    SellerFbsStock,
    WarehouseStockDetailed,
)

# --- Глобальные дефолты (переопределяются PricingPolicy) ---

DEFAULT_BUYOUT_RATE = 0.9          # ~90% заказов выкупают; остальное возвращается на склад
DEADBAND = 0.15                    # мёртвая зона контроллера (±15% от целевой скорости)
VELOCITY_WINDOW_DAYS = 7           # окно расчёта фактической скорости заказов
SHORT_WINDOW_DAYS = 3             # короткое окно для детектора волны
WAVE_RATIO = 1.30                  # short/mid выше этого в пик = волна спроса

MAX_DISCOUNT = 75                  # потолок скидки WB
MIN_DISCOUNT = 0

STEP_UP_NORMAL = 2                 # -пункты скидки в день (поднять цену)
STEP_UP_WAVE = 3                   # в подтверждённую декабрьскую волну — резче вверх
STEP_DOWN_NORMAL = 1               # +пункты скидки в день (снизить цену), мин. шаг
STEP_DOWN_TAIL = 3                 # после 26 дек спрос обвалился — быстрее вниз
STEP_DOWN_ENDGAME = 4             # финальная распродажа

PRICE_NUDGE_PCT = 0.01             # шаг базовой цены, когда скидка упёрлась в границу


# --- Фазы сезона и сезонные веса ---

PHASE_PRESEASON = "preseason"
PHASE_NOVEMBER = "november"
PHASE_DEC_RAMP = "december_ramp"
PHASE_DEC_PEAK = "december_peak"
PHASE_DEC_TAIL = "december_tail"
PHASE_JANUARY = "january"
PHASE_FEBRUARY = "february"
PHASE_ENDGAME = "endgame"
PHASE_POSTSEASON = "postseason"

PHASE_LABELS = {
    PHASE_PRESEASON: "До сезона — держим цены",
    PHASE_NOVEMBER: "Ноябрь — спрос низкий, акции ради буста",
    PHASE_DEC_RAMP: "Декабрь, разгон (1–14)",
    PHASE_DEC_PEAK: "Декабрь, пик (15–26) — поднимаем цены",
    PHASE_DEC_TAIL: "После 26 дек — спрос обвалился, вниз",
    PHASE_JANUARY: "Январь — остаточный спрос",
    PHASE_FEBRUARY: "Февраль — целимся в 0",
    PHASE_ENDGAME: "Endgame — распродажа без пола цены",
    PHASE_POSTSEASON: "Вне сезона",
}


def default_target_zero_date(today: datetime.date) -> datetime.date:
    """Ближайшее 15 февраля не раньше сегодня — единый дедлайн выхода в 0."""
    candidate = datetime.date(today.year, 2, 15)
    if candidate < today:
        candidate = datetime.date(today.year + 1, 2, 15)
    return candidate


def hard_deadline_for(target_zero_date: datetime.date) -> datetime.date:
    """1 марта того же сезона — крайний срок (обязательная маркировка)."""
    return datetime.date(target_zero_date.year, 3, 1)


def season_start_for(target_zero_date: datetime.date) -> datetime.date:
    """1 ноября сезона, к которому относится дедлайн."""
    return datetime.date(target_zero_date.year - 1, 11, 1)


def get_season_phase(today: datetime.date, target_zero_date: datetime.date) -> str:
    season_start = season_start_for(target_zero_date)
    hard = hard_deadline_for(target_zero_date)
    endgame_start = max(target_zero_date - datetime.timedelta(days=7), datetime.date(target_zero_date.year, 2, 8))

    if today < season_start:
        return PHASE_PRESEASON
    if today > hard:
        return PHASE_POSTSEASON
    if today >= endgame_start:
        return PHASE_ENDGAME
    m, d = today.month, today.day
    if m == 11:
        return PHASE_NOVEMBER
    if m == 12:
        if d <= 14:
            return PHASE_DEC_RAMP
        if d <= 26:
            return PHASE_DEC_PEAK
        return PHASE_DEC_TAIL
    if m == 1:
        return PHASE_JANUARY
    return PHASE_FEBRUARY


def daily_demand_weight(d: datetime.date) -> float:
    """
    Относительный вес ожидаемого спроса дня d (для распределения остатка по дням).
    Калибровка по истории Toycloud: дек ≈ 50% сезона, пик 15–26 дек, обвал после 26.
    Вне сезона (ноя–фев) вес 0 — робот не планирует продажи до старта.
    """
    m, day = d.month, d.day
    if m == 11:
        return 1.0
    if m == 12:
        if day <= 14:
            return 1.5 + (4.0 - 1.5) * (day - 1) / 13.0   # разгон 1.5 → 4.0
        if day <= 26:
            return 7.0                                     # инеластичный пик
        return 1.2                                         # обвал перед НГ
    if m == 1:
        return 1.6
    if m == 2:
        return 1.3 if day <= 15 else 1.0
    return 0.0


def remaining_demand_weight(today: datetime.date, deadline: datetime.date) -> float:
    total = 0.0
    d = today
    while d <= deadline:
        total += daily_demand_weight(d)
        d += datetime.timedelta(days=1)
    return total


# --- Сбор данных (локально, без обращения к WB API) ---

def _fbo_stock_by_nm(seller: SellerAccount) -> dict[int, int]:
    result: dict[int, int] = defaultdict(int)
    rows = (
        WarehouseStockDetailed.objects
        .filter(seller=seller, quantity__gt=0)
        .values_list("nm_id", "quantity")
    )
    for nm_id, qty in rows:
        result[int(nm_id)] += int(qty or 0)
    return result


def _fbs_stock_by_nm(seller: SellerAccount) -> dict[int, int]:
    """FBS хранится по chrt_id; мапим chrt_id → nm_id через ProductCardSize."""
    chrt_to_nm: dict[int, int] = {}
    for chrt_id, nm_id in (
        ProductCardSize.objects
        .filter(seller=seller)
        .exclude(nm_id__isnull=True)
        .values_list("chrt_id", "nm_id")
    ):
        chrt_to_nm[int(chrt_id)] = int(nm_id)

    result: dict[int, int] = defaultdict(int)
    for chrt_id, amount in (
        SellerFbsStock.objects
        .filter(seller=seller, amount__gt=0)
        .values_list("chrt_id", "amount")
    ):
        nm_id = chrt_to_nm.get(int(chrt_id))
        if nm_id is not None:
            result[nm_id] += int(amount or 0)
    return result


def _velocity_by_nm(seller: SellerAccount, now_dt, window_days: int) -> dict[int, float]:
    """Среднедневная скорость ЗАКАЗОВ (без отмен) за окно, шт/день."""
    since = now_dt - datetime.timedelta(days=window_days)
    rows = (
        Order.objects
        .filter(seller=seller, order_date__gte=since, is_cancel=False)
        .values("nm_id")
        .annotate(cnt=Count("id"))
    )
    return {int(r["nm_id"]): float(r["cnt"]) / float(window_days) for r in rows}


def _current_prices_by_nm(seller: SellerAccount) -> dict[int, dict]:
    """
    Текущая цена/скидка по nm_id из локальной ProductSizePrice (последний синк).
    Размеров нет → обычно одна строка на nm_id; если несколько, берём с макс. net.
    """
    result: dict[int, dict] = {}
    for row in ProductSizePrice.objects.filter(seller=seller).values(
        "nm_id", "price", "discounted_price", "discount_percent"
    ):
        nm_id = int(row["nm_id"])
        price = row.get("price")
        if price is None:
            continue
        discount = row.get("discount_percent") or 0.0
        discounted = row.get("discounted_price")
        if discounted is None:
            discounted = float(price) * (1.0 - float(discount) / 100.0)
        prev = result.get(nm_id)
        if prev is None or float(discounted) > float(prev["discounted_price"]):
            result[nm_id] = {
                "price": float(price),
                "discount": float(discount),
                "discounted_price": float(discounted),
            }
    return result


def _products_by_nm(seller: SellerAccount) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for p in Product.objects.filter(seller=seller).values(
        "nm_id", "vendor_code", "title", "photo_url", "purchase_price"
    ):
        result[int(p["nm_id"])] = p
    return result


def _policies_by_nm(seller: SellerAccount) -> dict[int, PricingPolicy]:
    return {int(p.nm_id): p for p in PricingPolicy.objects.filter(seller=seller)}


# --- Контроллер ---

@dataclass
class Decision:
    nm_id: int
    vendor_code: str = ""
    title: str = ""
    photo_url: str = ""
    stock_fbo: int = 0
    stock_fbs: int = 0
    stock_total: int = 0
    velocity_7d: float = 0.0
    velocity_short: float = 0.0
    required_orders_rate: float = 0.0
    days_of_cover: float | None = None
    days_left: int = 0
    current_price: float | None = None
    current_discount: float | None = None
    current_net: float | None = None
    new_price: float | None = None
    new_discount: float | None = None
    new_net: float | None = None
    action: str = "hold"          # raise | lower | hold | no_data | no_stock
    reason: str = ""
    is_wave: bool = False
    floor_hit: bool = False

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _resolve_policy_value(policy, attr, default):
    if policy is None:
        return default
    val = getattr(policy, attr, None)
    return default if val is None else val


def build_plan(
    seller: SellerAccount,
    *,
    today: datetime.date | None = None,
    target_zero_date: datetime.date | None = None,
) -> dict:
    """
    Считает план цен по всем артикулам продавца (dry-run, только локальные данные).
    Ничего не пишет в WB. Возвращает {phase, today, deadline, decisions, summary}.
    """
    if today is None:
        now_dt = timezone.now()
        today = timezone.localdate()
    else:
        # Явная дата (бэктест/--date): окно скорости заказов привязываем к ней же.
        now_dt = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time.max),
            timezone.get_current_timezone(),
        )
    target_zero_date = target_zero_date or default_target_zero_date(today)
    hard = hard_deadline_for(target_zero_date)
    phase = get_season_phase(today, target_zero_date)
    days_left = max(0, (target_zero_date - today).days)

    fbo = _fbo_stock_by_nm(seller)
    fbs = _fbs_stock_by_nm(seller)
    vel7 = _velocity_by_nm(seller, now_dt, VELOCITY_WINDOW_DAYS)
    vel_short = _velocity_by_nm(seller, now_dt, SHORT_WINDOW_DAYS)
    prices = _current_prices_by_nm(seller)
    products = _products_by_nm(seller)
    policies = _policies_by_nm(seller)

    remaining_w = remaining_demand_weight(today, target_zero_date) or 1.0
    today_w = daily_demand_weight(today)

    all_nm = set(fbo) | set(fbs)
    decisions: list[Decision] = []

    for nm_id in all_nm:
        policy = policies.get(nm_id)
        if policy is not None and (not policy.enabled or policy.mode == PricingPolicy.MODE_OFF):
            continue

        prod = products.get(nm_id, {})
        price_info = prices.get(nm_id)
        stock_fbo = int(fbo.get(nm_id, 0))
        stock_fbs = int(fbs.get(nm_id, 0))
        stock_total = stock_fbo + stock_fbs

        dec = Decision(
            nm_id=nm_id,
            vendor_code=(prod.get("vendor_code") or (policy.vendor_code if policy else "") or ""),
            title=(prod.get("title") or ""),
            photo_url=(prod.get("photo_url") or ""),
            stock_fbo=stock_fbo,
            stock_fbs=stock_fbs,
            stock_total=stock_total,
            velocity_7d=round(vel7.get(nm_id, 0.0), 3),
            velocity_short=round(vel_short.get(nm_id, 0.0), 3),
            days_left=days_left,
        )

        if price_info:
            dec.current_price = round(price_info["price"], 2)
            dec.current_discount = round(price_info["discount"], 1)
            dec.current_net = round(price_info["discounted_price"], 2)

        if stock_total <= 0:
            dec.action = "no_stock"
            dec.reason = "Нет остатка"
            decisions.append(dec)
            continue

        actual = vel7.get(nm_id, 0.0)
        dec.days_of_cover = round(stock_total / actual, 1) if actual > 0 else None

        # Проекция: чтобы физически распродать stock_total к дедлайну, с учётом
        # возвратов нужно чуть больше ЗАКАЗОВ (units / buyout_rate).
        buyout_rate = float(_resolve_policy_value(policy, "buyout_rate", DEFAULT_BUYOUT_RATE)) or DEFAULT_BUYOUT_RATE
        required_units_rate = stock_total * today_w / remaining_w if today_w > 0 else 0.0
        required_orders_rate = required_units_rate / buyout_rate if buyout_rate > 0 else required_units_rate
        dec.required_orders_rate = round(required_orders_rate, 3)

        # Вне сезона робот держит цены.
        if phase in (PHASE_PRESEASON, PHASE_POSTSEASON):
            dec.action = "hold"
            dec.reason = PHASE_LABELS[phase]
            decisions.append(dec)
            continue

        if price_info is None:
            dec.action = "no_data"
            dec.reason = "Нет текущей цены (синхронизируйте цены)"
            decisions.append(dec)
            continue

        is_endgame = phase == PHASE_ENDGAME
        max_up = int(_resolve_policy_value(policy, "max_step_up_points", STEP_UP_NORMAL))
        max_down = int(_resolve_policy_value(policy, "max_step_down_points", STEP_DOWN_NORMAL))

        # Детектор декабрьской волны.
        mid = actual
        short = vel_short.get(nm_id, 0.0)
        dec.is_wave = bool(phase == PHASE_DEC_PEAK and mid > 0 and short > mid * WAVE_RATIO)

        # Решение по соотношению факт/цель.
        direction = "hold"
        if required_orders_rate <= 0:
            direction = "hold"
        elif actual <= 0:
            direction = "lower"        # не продаётся, а надо — снижаем цену
        else:
            ratio = actual / required_orders_rate
            if ratio > 1.0 + DEADBAND:
                direction = "raise"
            elif ratio < 1.0 - DEADBAND:
                direction = "lower"
            else:
                direction = "hold"

        # Фазовые смещения.
        if phase == PHASE_DEC_TAIL and direction != "raise":
            direction = "lower"        # после 26 дек — уходим вниз
        if is_endgame:
            direction = "lower"        # финальная распродажа доминирует

        # Величина шага (в пунктах скидки).
        if direction == "raise":
            step = min(max_up, STEP_UP_WAVE if dec.is_wave else STEP_UP_NORMAL)
        elif direction == "lower":
            if is_endgame:
                step = max(max_down, STEP_DOWN_ENDGAME)
            elif phase == PHASE_DEC_TAIL:
                step = max(max_down, STEP_DOWN_TAIL)
            else:
                step = max(max_down, STEP_DOWN_NORMAL)
        else:
            step = 0

        base_price = float(price_info["price"])
        cur_disc = float(price_info["discount"])
        new_price = base_price
        new_disc = cur_disc

        if direction == "raise":
            new_disc = cur_disc - step
            if new_disc < MIN_DISCOUNT:
                new_disc = MIN_DISCOUNT
                new_price = round(base_price * (1.0 + PRICE_NUDGE_PCT))
        elif direction == "lower":
            new_disc = cur_disc + step
            if new_disc > MAX_DISCOUNT:
                new_disc = MAX_DISCOUNT
                if is_endgame:
                    new_price = round(base_price * (1.0 - PRICE_NUDGE_PCT))

        new_net = new_price * (1.0 - new_disc / 100.0)

        # Пол цены (вне endgame). floor из политики; иначе — закупка (если известна).
        floor_price = _resolve_policy_value(policy, "floor_price", None)
        if floor_price is None:
            pp = prod.get("purchase_price")
            floor_price = float(pp) if pp else None
        if floor_price and not is_endgame and new_net < floor_price and direction == "lower":
            # Не опускаем net ниже пола: подбираем макс. допустимую скидку.
            allowed_disc = max(MIN_DISCOUNT, min(MAX_DISCOUNT, (1.0 - floor_price / base_price) * 100.0))
            if allowed_disc < new_disc:
                new_disc = round(allowed_disc)
                new_net = base_price * (1.0 - new_disc / 100.0)
                dec.floor_hit = True

        # Потолок цены.
        ceiling_price = _resolve_policy_value(policy, "ceiling_price", None)
        if ceiling_price and new_net > ceiling_price and direction == "raise":
            allowed_disc = max(MIN_DISCOUNT, (1.0 - ceiling_price / base_price) * 100.0)
            if allowed_disc > new_disc:
                new_disc = round(allowed_disc)
                new_net = base_price * (1.0 - new_disc / 100.0)

        new_disc = int(round(max(MIN_DISCOUNT, min(MAX_DISCOUNT, new_disc))))
        new_price = int(round(new_price))
        new_net = new_price * (1.0 - new_disc / 100.0)

        changed = (new_disc != int(round(cur_disc))) or (new_price != int(round(base_price)))
        dec.action = direction if changed else "hold"
        dec.new_price = new_price
        dec.new_discount = new_disc
        dec.new_net = round(new_net, 2)

        # Причина (человекочитаемая).
        if dec.action == "raise":
            dec.reason = "Волна спроса — резче вверх" if dec.is_wave else "Продаётся быстрее плана — вверх"
        elif dec.action == "lower":
            if is_endgame:
                dec.reason = "Endgame — сливаем в 0"
            elif phase == PHASE_DEC_TAIL:
                dec.reason = "Спрос после 26 дек упал — вниз"
            elif actual <= 0:
                dec.reason = "Нет продаж, а надо продавать — вниз"
            else:
                dec.reason = "Отстаём от плана — вниз"
            if dec.floor_hit:
                dec.reason += " (упёрлись в пол)"
        else:
            dec.reason = "В пределах плана — держим"

        decisions.append(dec)

    # Сводка.
    decisions.sort(key=lambda x: (x.action not in ("raise", "lower"), -x.stock_total))
    summary = {
        "total_sku": len(decisions),
        "raise": sum(1 for d in decisions if d.action == "raise"),
        "lower": sum(1 for d in decisions if d.action == "lower"),
        "hold": sum(1 for d in decisions if d.action == "hold"),
        "no_stock": sum(1 for d in decisions if d.action == "no_stock"),
        "no_data": sum(1 for d in decisions if d.action == "no_data"),
        "total_stock": sum(d.stock_total for d in decisions),
        "phase": phase,
        "phase_label": PHASE_LABELS.get(phase, phase),
    }

    return {
        "phase": phase,
        "phase_label": PHASE_LABELS.get(phase, phase),
        "today": today.isoformat(),
        "target_zero_date": target_zero_date.isoformat(),
        "hard_deadline": hard.isoformat(),
        "days_left": days_left,
        "decisions": [d.as_dict() for d in decisions],
        "summary": summary,
    }


def apply_plan(seller: SellerAccount, decisions: list[dict], *, only_changed: bool = True) -> dict:
    """
    Пишет цены в WB по плану. Вызывается ТОЛЬКО осознанно (--apply / mode=auto).
    Читает back-цены отдельным синком уже не здесь — верификацию делает вызывающий.
    """
    from wb_api.client import WBDiscountsPricesClient

    items = []
    for d in decisions:
        action = d.get("action")
        if only_changed and action not in ("raise", "lower"):
            continue
        if d.get("new_price") is None or d.get("new_discount") is None:
            continue
        items.append({
            "nmID": int(d["nm_id"]),
            "price": int(d["new_price"]),
            "discount": int(d["new_discount"]),
        })

    if not items:
        return {"sent": 0, "response": None}

    client = WBDiscountsPricesClient(seller.api_token_plain)
    response = client.set_prices_discounts(items)
    return {"sent": len(items), "response": response}
