from __future__ import annotations

from app.services.supply_recommendations.calculators import (
    calculate_baseline_logistics_cost,
    calculate_local_share,
    calculate_localization_index,
    calculate_net_effect,
    calculate_projected_local_orders,
    calculate_transit_cost,
    calculate_warehouse_logistics_cost,
)
from app.services.supply_recommendations.models import RegionScenarioInput, RegionScenarioResult

BORDERLINE_EPSILON_RUB = 1.0


def evaluate_region_scenario(
    scenario_input: RegionScenarioInput,
    total_orders: int,
    current_local_orders: int,
    current_localization_index: float,
    penalty_factor: float = 1.0,
    current_total_cost_override: float | None = None,
    projected_global_logistics_cost_override: float | None = None,
) -> RegionScenarioResult:
    """
    Evaluates one regional supply scenario and returns economic impact.

    The function is pure and isolated from ORM/API.
    """
    current_total_cost = (
        round(float(current_total_cost_override), 2)
        if current_total_cost_override is not None
        else calculate_baseline_logistics_cost(
            total_orders=total_orders,
            base_logistics_per_order=scenario_input.base_logistics_per_order,
            localization_index=current_localization_index,
        )
    )

    # Для прогноза локализации учитываем только нелокальные заказы региона:
    # именно они становятся локальными после размещения.
    newly_localized_orders = max(
        min(int(scenario_input.non_local_orders_count or 0), int(scenario_input.orders_count)),
        0,
    )
    if newly_localized_orders <= 0 and not scenario_input.current_local:
        # Fallback для тестовых/устаревших вызовов без non_local_orders_count.
        newly_localized_orders = scenario_input.orders_count
    projected_local_orders = calculate_projected_local_orders(current_local_orders, newly_localized_orders)
    projected_local_orders = min(projected_local_orders, total_orders)

    projected_local_share = calculate_local_share(total_orders, projected_local_orders)
    projected_localization_index = calculate_localization_index(
        local_share=projected_local_share,
        penalty_factor=penalty_factor,
    )

    projected_global_logistics_cost = (
        round(float(projected_global_logistics_cost_override), 2)
        if projected_global_logistics_cost_override is not None
        else calculate_baseline_logistics_cost(
            total_orders=total_orders,
            base_logistics_per_order=scenario_input.base_logistics_per_order,
            localization_index=projected_localization_index,
        )
    )

    projected_transit_cost = calculate_transit_cost(
        orders_count=newly_localized_orders,
        avg_volume_liters=scenario_input.avg_volume_liters,
        price_per_liter=scenario_input.transit_price_per_liter,
    )

    projected_warehouse_logistics_cost = calculate_warehouse_logistics_cost(
        orders_count=newly_localized_orders,
        base_logistics_per_order=scenario_input.base_logistics_per_order,
        warehouse_coef=scenario_input.warehouse_coef,
    )

    baseline_region_logistics_cost = calculate_warehouse_logistics_cost(
        orders_count=newly_localized_orders,
        base_logistics_per_order=scenario_input.base_logistics_per_order,
        warehouse_coef=scenario_input.baseline_warehouse_coef,
    )
    projected_extra_warehouse_cost = round(
        projected_warehouse_logistics_cost - baseline_region_logistics_cost,
        2,
    )

    projected_localization_savings = round(
        max(current_total_cost - projected_global_logistics_cost, 0.0),
        2,
    )

    projected_total_cost = round(
        projected_global_logistics_cost + projected_transit_cost + projected_extra_warehouse_cost,
        2,
    )

    net_effect = calculate_net_effect(
        current_total_cost=current_total_cost,
        projected_total_cost=projected_total_cost,
    )

    if abs(net_effect) <= BORDERLINE_EPSILON_RUB:
        comment = "Пограничный сценарий, нужна проверка"
        recommended = False
    elif net_effect > 0:
        comment = "Рекомендуется поставка"
        recommended = True
    else:
        comment = "Поставка невыгодна"
        recommended = False

    return RegionScenarioResult(
        region_name=scenario_input.region_name,
        current_orders_count=scenario_input.orders_count,
        non_local_orders=scenario_input.non_local_orders_count,
        current_total_cost=current_total_cost,
        projected_total_cost=projected_total_cost,
        projected_transit_cost=projected_transit_cost,
        projected_extra_warehouse_cost=projected_extra_warehouse_cost,
        projected_localization_savings=projected_localization_savings,
        net_effect=net_effect,
        recommended=recommended,
        target_warehouse_name=scenario_input.target_warehouse_name,
        comment=comment,
    )
