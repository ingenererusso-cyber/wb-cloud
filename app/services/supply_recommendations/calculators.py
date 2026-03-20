from __future__ import annotations


def _validate_non_negative_int(name: str, value: int) -> None:
    if not isinstance(value, int):
        raise ValueError(f"{name} must be int")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


def _validate_non_negative_float(name: str, value: float) -> None:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


def calculate_local_share(total_orders: int, local_orders: int) -> float:
    _validate_non_negative_int("total_orders", total_orders)
    _validate_non_negative_int("local_orders", local_orders)
    if local_orders > total_orders:
        raise ValueError("local_orders must be <= total_orders")
    if total_orders == 0:
        return 0.0
    return local_orders / total_orders


def calculate_non_local_orders(total_orders: int, local_orders: int) -> int:
    _validate_non_negative_int("total_orders", total_orders)
    _validate_non_negative_int("local_orders", local_orders)
    if local_orders > total_orders:
        raise ValueError("local_orders must be <= total_orders")
    return total_orders - local_orders


def calculate_transit_cost(orders_count: int, avg_volume_liters: float, price_per_liter: float) -> float:
    _validate_non_negative_int("orders_count", orders_count)
    _validate_non_negative_float("avg_volume_liters", avg_volume_liters)
    _validate_non_negative_float("price_per_liter", price_per_liter)
    return round(orders_count * avg_volume_liters * price_per_liter, 2)


def calculate_warehouse_logistics_cost(
    orders_count: int,
    base_logistics_per_order: float,
    warehouse_coef: float,
) -> float:
    _validate_non_negative_int("orders_count", orders_count)
    _validate_non_negative_float("base_logistics_per_order", base_logistics_per_order)
    _validate_non_negative_float("warehouse_coef", warehouse_coef)
    return round(orders_count * base_logistics_per_order * warehouse_coef, 2)


def calculate_baseline_logistics_cost(
    total_orders: int,
    base_logistics_per_order: float,
    localization_index: float,
) -> float:
    _validate_non_negative_int("total_orders", total_orders)
    _validate_non_negative_float("base_logistics_per_order", base_logistics_per_order)
    _validate_non_negative_float("localization_index", localization_index)
    return round(total_orders * base_logistics_per_order * localization_index, 2)


def calculate_projected_local_orders(current_local_orders: int, newly_localized_orders: int) -> int:
    _validate_non_negative_int("current_local_orders", current_local_orders)
    _validate_non_negative_int("newly_localized_orders", newly_localized_orders)
    return current_local_orders + newly_localized_orders


def calculate_net_effect(current_total_cost: float, projected_total_cost: float) -> float:
    _validate_non_negative_float("current_total_cost", current_total_cost)
    _validate_non_negative_float("projected_total_cost", projected_total_cost)
    return round(current_total_cost - projected_total_cost, 2)


def calculate_localization_index(local_share: float, penalty_factor: float = 1.0) -> float:
    _validate_non_negative_float("local_share", local_share)
    _validate_non_negative_float("penalty_factor", penalty_factor)
    if local_share > 1:
        raise ValueError("local_share must be in range [0, 1]")
    return round(1 + (1 - local_share) * penalty_factor, 6)
