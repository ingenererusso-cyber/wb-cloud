from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date
from typing import Dict, List

from app.services.supply_recommendations.calculators import (
    calculate_baseline_logistics_cost,
    calculate_local_share,
    calculate_localization_index,
    calculate_projected_local_orders,
    calculate_transit_cost,
    calculate_warehouse_logistics_cost,
)
from app.services.supply_recommendations.models import (
    OrderAggregate,
    RegionScenarioInput,
    RegionScenarioResult,
    TransitTariff,
    WarehouseOption,
    WarehouseCoefficient,
)
from app.services.supply_recommendations.scenarios import evaluate_region_scenario

logger = logging.getLogger(__name__)


KTR_SWITCH_DATE = date(2026, 3, 23)
KTR_TABLE = (
    (0.00, 4.99, 2.00, 2.00),
    (5.00, 9.99, 1.95, 1.80),
    (10.00, 14.99, 1.90, 1.75),
    (15.00, 19.99, 1.85, 1.70),
    (20.00, 24.99, 1.75, 1.60),
    (25.00, 29.99, 1.65, 1.55),
    (30.00, 34.99, 1.55, 1.50),
    (35.00, 39.99, 1.45, 1.40),
    (40.00, 44.99, 1.35, 1.30),
    (45.00, 49.99, 1.25, 1.20),
    (50.00, 54.99, 1.15, 1.10),
    (55.00, 59.99, 1.05, 1.05),
    (60.00, 64.99, 1.00, 1.00),
    (65.00, 69.99, 1.00, 1.00),
    (70.00, 74.99, 1.00, 1.00),
    (75.00, 79.99, 0.95, 0.90),
    (80.00, 84.99, 0.85, 0.80),
    (85.00, 89.99, 0.75, 0.70),
    (90.00, 94.99, 0.65, 0.60),
    (95.00, 100.00, 0.50, 0.50),
)


def get_ktr_for_share(local_share_percent: float, as_of_date: date) -> float:
    share = max(0.0, min(100.0, float(local_share_percent)))
    use_before_column = as_of_date < KTR_SWITCH_DATE
    idx = 2 if use_before_column else 3
    for min_share, max_share, ktr_before, ktr_after in KTR_TABLE:
        if min_share <= share <= max_share:
            return float((ktr_before, ktr_after)[idx - 2])
    return 0.50


def _normalize(value: str) -> str:
    return (value or "").strip()


def _normalize_region(value: str) -> str:
    normalized = _normalize(value)
    if not normalized:
        return ""
    if "Юж" in normalized or "Кавказ" in normalized:
        return "Юг"
    if "Сибир" in normalized or "Дальневост" in normalized:
        return "Восток"
    return normalized


def _build_warehouse_region_map(warehouse_coefficients: List[WarehouseCoefficient]) -> Dict[str, str]:
    warehouse_region_map: Dict[str, str] = {}
    for item in warehouse_coefficients:
        warehouse_name = _normalize(item.warehouse_name)
        region_name = _normalize_region(item.region_name)
        if not warehouse_name or not region_name:
            continue
        if warehouse_name not in warehouse_region_map:
            warehouse_region_map[warehouse_name] = region_name
    return warehouse_region_map


def _find_region_warehouse_coefficient(
    region_name: str,
    warehouse_coefficients: List[WarehouseCoefficient],
) -> WarehouseCoefficient | None:
    normalized_region = _normalize_region(region_name)
    candidates = [item for item in warehouse_coefficients if _normalize_region(item.region_name) == normalized_region]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.logistics_coef)


def _find_warehouse_coefficient_by_name(
    warehouse_name: str,
    warehouse_coefficients: List[WarehouseCoefficient],
) -> WarehouseCoefficient | None:
    normalized_name = _normalize(warehouse_name)
    if not normalized_name:
        return None
    for item in warehouse_coefficients:
        if _normalize(item.warehouse_name) == normalized_name:
            return item
    return None


def _find_transit_tariff(region_name: str, transit_tariffs: List[TransitTariff]) -> TransitTariff | None:
    normalized_region = _normalize_region(region_name)
    for item in transit_tariffs:
        if _normalize_region(item.target_region) == normalized_region:
            return item
    return None


def _group_aggregates_by_region(order_aggregates: List[OrderAggregate]) -> Dict[str, List[OrderAggregate]]:
    grouped: Dict[str, List[OrderAggregate]] = defaultdict(list)
    for item in order_aggregates:
        region_name = _normalize_region(item.order_region)
        if not region_name:
            continue
        grouped[region_name].append(item)
    return grouped


def _calc_total_orders(order_aggregates: List[OrderAggregate]) -> int:
    return sum(max(item.orders_count, 0) for item in order_aggregates)


def _estimate_current_local_orders(
    order_aggregates: List[OrderAggregate],
    warehouse_region_map: Dict[str, str],
) -> int:
    # Единственный источник истины по локальности — флаг Order.is_local,
    # предварительно агрегированный в local_orders_count.
    return sum(max(min(item.local_orders_count, item.orders_count), 0) for item in order_aggregates)


def _calc_region_orders_count(region_items: List[OrderAggregate]) -> int:
    return sum(max(item.orders_count, 0) for item in region_items)


def _calc_region_local_orders_count(region_items: List[OrderAggregate]) -> int:
    return sum(max(min(item.local_orders_count, item.orders_count), 0) for item in region_items)


def _calc_region_avg_volume(region_items: List[OrderAggregate]) -> float:
    total_orders = _calc_region_orders_count(region_items)
    if total_orders <= 0:
        return 0.0
    weighted_sum = sum(max(item.orders_count, 0) * max(item.avg_volume_liters, 0.0) for item in region_items)
    return round(weighted_sum / total_orders, 6)


def _is_region_currently_local(region_items: List[OrderAggregate], warehouse_region_map: Dict[str, str]) -> bool:
    total_orders = _calc_region_orders_count(region_items)
    if total_orders <= 0:
        return False
    local_orders = _calc_region_local_orders_count(region_items)
    return local_orders == total_orders


def _calc_newly_localized_orders(region_orders: int, non_local_orders: int, current_local: bool) -> int:
    value = max(min(int(non_local_orders or 0), int(region_orders)), 0)
    if value <= 0 and not current_local:
        return int(region_orders)
    return value


def _build_warehouse_options(
    region_name: str,
    transit_options: List[TransitTariff],
    warehouse_coefficients: List[WarehouseCoefficient],
    non_local_orders_count: int,
    orders_count: int,
    avg_volume_liters: float,
    base_logistics_per_order: float,
    baseline_warehouse_coef: float,
) -> List[WarehouseOption]:
    newly_localized_orders = _calc_newly_localized_orders(
        region_orders=orders_count,
        non_local_orders=non_local_orders_count,
        current_local=(non_local_orders_count <= 0),
    )
    if newly_localized_orders <= 0:
        return []

    baseline_region_logistics_cost = calculate_warehouse_logistics_cost(
        orders_count=newly_localized_orders,
        base_logistics_per_order=base_logistics_per_order,
        warehouse_coef=baseline_warehouse_coef,
    )

    options: List[WarehouseOption] = []
    for transit_item in transit_options:
        if transit_item.target_warehouse_name:
            warehouse_coef_item = _find_warehouse_coefficient_by_name(
                transit_item.target_warehouse_name,
                warehouse_coefficients,
            )
            if warehouse_coef_item is None:
                warehouse_coef_item = WarehouseCoefficient(
                    warehouse_name=transit_item.target_warehouse_name,
                    region_name=region_name,
                    logistics_coef=baseline_warehouse_coef,
                    storage_coef=None,
                )
        else:
            warehouse_coef_item = _find_region_warehouse_coefficient(region_name, warehouse_coefficients)
            if warehouse_coef_item is None:
                warehouse_coef_item = WarehouseCoefficient(
                    warehouse_name="Базовый склад",
                    region_name=region_name,
                    logistics_coef=baseline_warehouse_coef,
                    storage_coef=None,
                )

        projected_transit_cost = calculate_transit_cost(
            orders_count=newly_localized_orders,
            avg_volume_liters=avg_volume_liters,
            price_per_liter=transit_item.price_per_liter,
        )
        projected_warehouse_logistics_cost = calculate_warehouse_logistics_cost(
            orders_count=newly_localized_orders,
            base_logistics_per_order=base_logistics_per_order,
            warehouse_coef=warehouse_coef_item.logistics_coef,
        )
        projected_extra_warehouse_cost = round(
            projected_warehouse_logistics_cost - baseline_region_logistics_cost,
            2,
        )
        total_additional_cost = round(projected_transit_cost + projected_extra_warehouse_cost, 2)

        options.append(
            WarehouseOption(
                warehouse_name=warehouse_coef_item.warehouse_name,
                transit_price_per_liter=float(transit_item.price_per_liter),
                warehouse_coef=float(warehouse_coef_item.logistics_coef),
                projected_transit_cost=round(float(projected_transit_cost), 2),
                projected_extra_warehouse_cost=projected_extra_warehouse_cost,
                total_additional_cost=total_additional_cost,
            )
        )

    options.sort(key=lambda item: (item.total_additional_cost, item.projected_extra_warehouse_cost, item.warehouse_name))
    return options


def build_supply_recommendations(
    order_aggregates: List[OrderAggregate],
    warehouse_coefficients: List[WarehouseCoefficient],
    transit_tariffs: List[TransitTariff],
    base_logistics_per_order: float,
    penalty_factor: float = 1.0,
    missing_transit_comment: str | None = None,
    baseline_warehouse_coef: float = 1.0,
    as_of_date: date | None = None,
    current_theoretical_logistics_sum: float | None = None,
    transit_tariff_options_by_region: Dict[str, List[TransitTariff]] | None = None,
) -> List[RegionScenarioResult]:
    total_orders = _calc_total_orders(order_aggregates)
    if total_orders <= 0:
        return []

    warehouse_region_map = _build_warehouse_region_map(warehouse_coefficients)
    current_local_orders = _estimate_current_local_orders(order_aggregates, warehouse_region_map)
    current_local_share = calculate_local_share(total_orders, min(current_local_orders, total_orders))
    if as_of_date is not None:
        current_localization_index = get_ktr_for_share(current_local_share * 100.0, as_of_date=as_of_date)
    else:
        current_localization_index = calculate_localization_index(current_local_share, penalty_factor)

    if current_theoretical_logistics_sum is not None:
        current_total_cost = round(float(current_theoretical_logistics_sum) * float(current_localization_index), 2)
    else:
        current_total_cost = calculate_baseline_logistics_cost(
            total_orders=total_orders,
            base_logistics_per_order=base_logistics_per_order,
            localization_index=current_localization_index,
        )

    grouped_by_region = _group_aggregates_by_region(order_aggregates)
    results: List[RegionScenarioResult] = []

    for region_name, region_items in grouped_by_region.items():
        orders_count = _calc_region_orders_count(region_items)
        if orders_count <= 0:
            continue
        local_orders_count = _calc_region_local_orders_count(region_items)
        non_local_orders_count = max(orders_count - local_orders_count, 0)

        transit_tariff_item = _find_transit_tariff(region_name, transit_tariffs)
        if transit_tariff_item is None:
            logger.warning("Skip region '%s': no transit tariff", region_name)
            results.append(
                RegionScenarioResult(
                    region_name=region_name,
                    current_orders_count=orders_count,
                    non_local_orders=non_local_orders_count,
                    current_total_cost=round(current_total_cost, 2),
                    projected_total_cost=round(current_total_cost, 2),
                    projected_transit_cost=0.0,
                    projected_extra_warehouse_cost=0.0,
                    projected_localization_savings=0.0,
                    net_effect=0.0,
                    recommended=False,
                    target_warehouse_name="",
                    comment=missing_transit_comment or "Нет тарифа транзита для региона",
                )
            )
            continue

        warehouse_coef_item = None
        if transit_tariff_item.target_warehouse_name:
            warehouse_coef_item = _find_warehouse_coefficient_by_name(
                transit_tariff_item.target_warehouse_name,
                warehouse_coefficients,
            )
            if warehouse_coef_item is None:
                logger.warning(
                    "Region '%s': no coefficient for transit destination '%s', use baseline coefficient",
                    region_name,
                    transit_tariff_item.target_warehouse_name,
                )
                warehouse_coef_item = WarehouseCoefficient(
                    warehouse_name=transit_tariff_item.target_warehouse_name,
                    region_name=region_name,
                    logistics_coef=baseline_warehouse_coef,
                    storage_coef=None,
                )

        if warehouse_coef_item is None:
            warehouse_coef_item = _find_region_warehouse_coefficient(region_name, warehouse_coefficients)

        if warehouse_coef_item is None:
            logger.warning("Region '%s': no warehouse coefficient, use baseline coefficient", region_name)
            warehouse_coef_item = WarehouseCoefficient(
                warehouse_name=transit_tariff_item.target_warehouse_name or "Базовый склад",
                region_name=region_name,
                logistics_coef=baseline_warehouse_coef,
                storage_coef=None,
            )

        scenario_input = RegionScenarioInput(
            region_name=region_name,
            orders_count=orders_count,
            avg_volume_liters=_calc_region_avg_volume(region_items),
            current_local=_is_region_currently_local(region_items, warehouse_region_map),
            target_warehouse_name=warehouse_coef_item.warehouse_name,
            warehouse_coef=warehouse_coef_item.logistics_coef,
            baseline_warehouse_coef=baseline_warehouse_coef,
            transit_price_per_liter=transit_tariff_item.price_per_liter,
            base_logistics_per_order=base_logistics_per_order,
            non_local_orders_count=non_local_orders_count,
        )

        result = evaluate_region_scenario(
            scenario_input=scenario_input,
            total_orders=total_orders,
            current_local_orders=current_local_orders,
            current_localization_index=current_localization_index,
            penalty_factor=penalty_factor,
            current_total_cost_override=current_total_cost,
            projected_global_logistics_cost_override=(
                round(
                    float(current_theoretical_logistics_sum)
                    * float(
                        get_ktr_for_share(
                            calculate_local_share(
                                total_orders,
                                min(
                                    calculate_projected_local_orders(
                                        current_local_orders,
                                        max(
                                            min(
                                                int(scenario_input.non_local_orders_count or 0),
                                                int(scenario_input.orders_count),
                                            ),
                                            0,
                                        ),
                                    ),
                                    total_orders,
                                ),
                            )
                            * 100.0,
                            as_of_date=as_of_date,
                        )
                    ),
                    2,
                )
                if (current_theoretical_logistics_sum is not None and as_of_date is not None)
                else None
            ),
        )
        normalized_region_name = _normalize_region(region_name)
        region_options = []
        if transit_tariff_options_by_region is not None:
            region_options = transit_tariff_options_by_region.get(normalized_region_name, [])
        if not region_options:
            region_options = [transit_tariff_item]
        warehouse_options = _build_warehouse_options(
            region_name=region_name,
            transit_options=region_options,
            warehouse_coefficients=warehouse_coefficients,
            non_local_orders_count=non_local_orders_count,
            orders_count=orders_count,
            avg_volume_liters=_calc_region_avg_volume(region_items),
            base_logistics_per_order=base_logistics_per_order,
            baseline_warehouse_coef=baseline_warehouse_coef,
        )
        results.append(replace(result, warehouse_options=warehouse_options))

    results.sort(key=lambda item: item.net_effect, reverse=True)
    return results
