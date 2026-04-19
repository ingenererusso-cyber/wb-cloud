from __future__ import annotations

from datetime import date

DEFAULT_LOGISTICS_VOLUME_LITERS = 4.0
LOGISTICS_IRP_SWITCH_DATE = date(2026, 3, 23)

# (min_share, max_share, ktr_before_23_march, ktr_from_23_march, krp_from_23_march)
LOCALIZATION_COEFFICIENTS_TABLE = (
    (0.00, 4.99, 2.00, 2.00, 0.0250),
    (5.00, 9.99, 1.95, 1.80, 0.0245),
    (10.00, 14.99, 1.90, 1.75, 0.0235),
    (15.00, 19.99, 1.85, 1.70, 0.0230),
    (20.00, 24.99, 1.75, 1.60, 0.0225),
    (25.00, 29.99, 1.65, 1.55, 0.0220),
    (30.00, 34.99, 1.55, 1.50, 0.0215),
    (35.00, 39.99, 1.45, 1.40, 0.0210),
    (40.00, 44.99, 1.35, 1.30, 0.0210),
    (45.00, 49.99, 1.25, 1.20, 0.0205),
    (50.00, 54.99, 1.15, 1.10, 0.0205),
    (55.00, 59.99, 1.05, 1.05, 0.0200),
    (60.00, 64.99, 1.00, 1.00, 0.0000),
    (65.00, 69.99, 1.00, 1.00, 0.0000),
    (70.00, 74.99, 1.00, 1.00, 0.0000),
    (75.00, 79.99, 0.95, 0.90, 0.0000),
    (80.00, 84.99, 0.85, 0.80, 0.0000),
    (85.00, 89.99, 0.75, 0.70, 0.0000),
    (90.00, 94.99, 0.65, 0.60, 0.0000),
    (95.00, 100.00, 0.50, 0.50, 0.0000),
)


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().replace(" ", "").replace(",", ".")
        if not normalized or normalized in {"-", "—", "None", "null"}:
            return None
        value = normalized
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_volume_liters(
    volume_liters: float | None,
    default_volume_liters: float = DEFAULT_LOGISTICS_VOLUME_LITERS,
) -> float:
    parsed = _to_float(volume_liters)
    if parsed is not None and parsed > 0:
        return float(parsed)
    return max(float(default_volume_liters), 0.0)


def calculate_box_logistics_base_by_volume(volume_liters: float) -> float:
    """
    Базовая стоимость логистики для 1 единицы товара по объему (без коэффициента):
    - 0.001-0.200 л: 23 ₽
    - 0.201-0.400 л: 26 ₽
    - 0.401-0.600 л: 29 ₽
    - 0.601-0.800 л: 30 ₽
    - 0.801-1.000 л: 32 ₽
    - >1.000 л: 46 + 14 * (объем - 1)
    """
    volume = max(float(volume_liters or 0.0), 0.0)
    if volume <= 0:
        return 0.0
    if volume <= 0.2:
        return 23.0
    if volume <= 0.4:
        return 26.0
    if volume <= 0.6:
        return 29.0
    if volume <= 0.8:
        return 30.0
    if volume <= 1.0:
        return 32.0
    return 46.0 + 14.0 * (volume - 1.0)


def resolve_delivery_multiplier(
    api_coef_expr: float | None,
    fixed_delivery_coef: float | None,
    use_dlv_prc: bool = True,
) -> float:
    """
    Возвращает множитель логистики:
    - при use_dlv_prc=True: приоритет fixed_delivery_coef (dlv_prc), если > 0
    - иначе api_coef_expr / 100, если > 0
    - иначе 1.0
    """
    if use_dlv_prc:
        dlv_prc = _to_float(fixed_delivery_coef)
        if dlv_prc is not None and dlv_prc > 0:
            return float(dlv_prc)

    coef_expr = _to_float(api_coef_expr)
    if coef_expr is not None and coef_expr > 0:
        return float(coef_expr) / 100.0

    return 1.0


def get_ktr_for_share(local_share_percent: float, as_of_date: date) -> float:
    share = max(0.0, min(100.0, float(local_share_percent)))
    use_before_column = as_of_date < LOGISTICS_IRP_SWITCH_DATE
    for min_share, max_share, ktr_before, ktr_after, _krp_after in LOCALIZATION_COEFFICIENTS_TABLE:
        if min_share <= share <= max_share:
            return float(ktr_before if use_before_column else ktr_after)
    return 0.50


def get_krp_for_share(local_share_percent: float, as_of_date: date) -> float:
    # До 23 марта ИРП/КРП не применялся.
    if as_of_date < LOGISTICS_IRP_SWITCH_DATE:
        return 0.0
    share = max(0.0, min(100.0, float(local_share_percent)))
    for min_share, max_share, _ktr_before, _ktr_after, krp_after in LOCALIZATION_COEFFICIENTS_TABLE:
        if min_share <= share <= max_share:
            return float(krp_after)
    return 0.0


def calculate_theoretical_order_logistics(
    volume_liters: float | None,
    api_coef_expr: float | None = None,
    fixed_delivery_coef: float | None = None,
    *,
    use_dlv_prc: bool = True,
    default_volume_liters: float = DEFAULT_LOGISTICS_VOLUME_LITERS,
    as_of_date: date | None = None,
    retail_price_before_discount: float | None = None,
    irp_index: float = 0.0,
) -> float:
    """
    Единая формула теоретической логистики заказа:
      base_by_volume(volume_liters) * delivery_multiplier(api_coef_expr, dlv_prc).
    """
    resolved_volume = resolve_volume_liters(
        volume_liters=volume_liters,
        default_volume_liters=default_volume_liters,
    )
    base = calculate_box_logistics_base_by_volume(resolved_volume)
    if base <= 0:
        return 0.0
    multiplier = resolve_delivery_multiplier(
        api_coef_expr=api_coef_expr,
        fixed_delivery_coef=fixed_delivery_coef,
        use_dlv_prc=use_dlv_prc,
    )
    if multiplier <= 0:
        return 0.0
    logistics_cost = base * multiplier

    # С 23 марта добавляется компонент ИРП:
    # (базовая_логистика * коэф_склада) + (цена_товара * ИРП)
    # Здесь ИЛ не применяется — он учитывается отдельным множителем выше по пайплайну.
    if as_of_date is not None and as_of_date >= LOGISTICS_IRP_SWITCH_DATE:
        retail_price = _to_float(retail_price_before_discount) or 0.0
        irp = max(float(irp_index or 0.0), 0.0)
        logistics_cost += retail_price * irp

    return logistics_cost
