from __future__ import annotations

from datetime import date

from app.services.supply_recommendations.calculators import calculate_local_share
from app.services.supply_recommendations.loaders import (
    build_default_warehouse_coefficients,
    calculate_theoretical_logistics_sum_for_period,
    estimate_base_logistics_per_order_from_tariffs,
    get_warehouse_logistics_coef,
    list_available_transit_warehouses,
    list_regular_warehouses,
    load_transit_tariffs_from_directions,
    load_transit_tariffs_for_transit_warehouse,
    load_transit_tariff_options_for_transit_warehouse,
    load_transit_tariffs_from_tariffs,
    load_warehouse_coefficients_from_tariffs,
    load_order_aggregates,
)
from app.services.supply_recommendations.recommendations import build_supply_recommendations, get_ktr_for_share
from app.services.supply_recommendations.serializers import serialize_recommendations_for_dashboard
from core.models import SellerAccount, TransitDirectionTariff, WbAcceptanceCoefficient, WbWarehouseTariff


def get_dashboard_supply_recommendations(
    date_from: date,
    date_to: date,
    seller: SellerAccount | None = None,
    transit_warehouse: str | None = None,
    main_warehouse: str | None = None,
    include_food: bool = False,
    only_with_fbs_stock: bool = False,
    base_logistics_per_order: float | None = None,
    penalty_factor: float = 1.0,
) -> dict:
    """
    Service-layer facade for dashboard recommendations API.

    Loads source data, builds recommendations and returns serialized payload.
    """
    order_aggregates = load_order_aggregates(
        date_from=date_from,
        date_to=date_to,
        seller=seller,
        only_with_fbs_stock=only_with_fbs_stock,
    )
    if not order_aggregates:
        return serialize_recommendations_for_dashboard([])

    total_orders = sum(max(item.orders_count, 0) for item in order_aggregates)
    current_local_orders = sum(
        max(min(item.local_orders_count, item.orders_count), 0)
        for item in order_aggregates
    )

    selected_main_warehouse = (main_warehouse or "").strip() or None
    if transit_warehouse:
        transit_tariff_options_by_region = load_transit_tariff_options_for_transit_warehouse(
            transit_warehouse=transit_warehouse,
            order_aggregates=order_aggregates,
            seller=seller,
        )
        if not include_food:
            transit_tariff_options_by_region = {
                region: [item for item in items if not _is_food_warehouse_name(item.target_warehouse_name)]
                for region, items in transit_tariff_options_by_region.items()
            }
            transit_tariff_options_by_region = {
                region: items for region, items in transit_tariff_options_by_region.items() if items
            }
        transit_tariffs = load_transit_tariffs_for_transit_warehouse(
            transit_warehouse=transit_warehouse,
            order_aggregates=order_aggregates,
            seller=seller,
        )
        if not include_food:
            transit_tariffs = [item for item in transit_tariffs if not _is_food_warehouse_name(item.target_warehouse_name)]
    else:
        transit_tariff_options_by_region = {}
        transit_tariffs = load_transit_tariffs_from_directions(order_aggregates, seller=seller)
        if not transit_tariffs:
            transit_tariffs = load_transit_tariffs_from_tariffs(order_aggregates, seller=seller)

    extra_warehouses: list[str] = []
    if selected_main_warehouse:
        extra_warehouses.append(selected_main_warehouse)
    for tariff in transit_tariffs:
        if tariff.target_warehouse_name:
            extra_warehouses.append(tariff.target_warehouse_name)
    for options in transit_tariff_options_by_region.values():
        for option in options:
            if option.target_warehouse_name:
                extra_warehouses.append(option.target_warehouse_name)

    # Нужны коэффициенты не только складов текущих отгрузок, но и складов назначения
    # по транзитным направлениям, иначе "Доп. склад" может ошибочно становиться 0.
    warehouse_coefficients = load_warehouse_coefficients_from_tariffs(
        order_aggregates,
        seller=seller,
        extra_warehouses=extra_warehouses or None,
    )
    if not warehouse_coefficients:
        warehouse_coefficients = build_default_warehouse_coefficients(order_aggregates)

    effective_base_logistics = (
        float(base_logistics_per_order)
        if base_logistics_per_order is not None
        else estimate_base_logistics_per_order_from_tariffs(
            order_aggregates=order_aggregates,
            seller=seller,
            fallback_value=50.0,
        )
    )

    baseline_warehouse_coef = 1.0
    if selected_main_warehouse:
        loaded_coef = get_warehouse_logistics_coef(
            warehouse_name=selected_main_warehouse,
            order_aggregates=order_aggregates,
            seller=seller,
        )
        if loaded_coef is not None and loaded_coef > 0:
            baseline_warehouse_coef = loaded_coef

    current_theoretical_logistics_sum = calculate_theoretical_logistics_sum_for_period(
        date_from=date_from,
        date_to=date_to,
        seller=seller,
    )

    results = build_supply_recommendations(
        order_aggregates=order_aggregates,
        warehouse_coefficients=warehouse_coefficients,
        transit_tariffs=transit_tariffs,
        base_logistics_per_order=effective_base_logistics,
        penalty_factor=penalty_factor,
        baseline_warehouse_coef=baseline_warehouse_coef,
        as_of_date=date_to,
        current_theoretical_logistics_sum=current_theoretical_logistics_sum,
        missing_transit_comment=(
            "Для этого направления нет тарифа с выбранного транзитного склада. Выберите другой транзитный склад."
            if transit_warehouse else None
        ),
        transit_tariff_options_by_region=transit_tariff_options_by_region,
    )
    payload = serialize_recommendations_for_dashboard(results)
    # Суммарный плюс считаем как единый общий эффект локализации:
    # в локальные переводятся только нелокальные заказы регионов с recommended=True.
    if total_orders > 0:
        recommended_results = [item for item in results if item.recommended]
        recommended_non_local_orders = sum(
            max(int(item.non_local_orders), 0)
            for item in recommended_results
        )
        current_share = calculate_local_share(total_orders, min(current_local_orders, total_orders))
        projected_local_orders = min(current_local_orders + recommended_non_local_orders, total_orders)
        projected_share = calculate_local_share(total_orders, projected_local_orders)
        current_index = get_ktr_for_share(current_share * 100.0, as_of_date=date_to)
        projected_index = get_ktr_for_share(projected_share * 100.0, as_of_date=date_to)
        current_total_cost = round(float(current_theoretical_logistics_sum) * float(current_index), 2)
        projected_total_cost = round(float(current_theoretical_logistics_sum) * float(projected_index), 2)
        localization_effect = round(current_total_cost - projected_total_cost, 2)
        recommended_transit_cost = round(
            sum(float(item.projected_transit_cost or 0.0) for item in recommended_results),
            2,
        )
        recommended_extra_warehouse_cost = round(
            sum(float(item.projected_extra_warehouse_cost or 0.0) for item in recommended_results),
            2,
        )
        recommended_additional_cost = round(
            recommended_transit_cost + recommended_extra_warehouse_cost,
            2,
        )
        total_positive_effect = round(localization_effect - recommended_additional_cost, 2)

        payload["summary"]["total_positive_effect"] = total_positive_effect
        payload["summary"]["localization_effect"] = localization_effect
        payload["summary"]["recommended_transit_cost"] = recommended_transit_cost
        payload["summary"]["recommended_extra_warehouse_cost"] = recommended_extra_warehouse_cost
        payload["summary"]["recommended_additional_cost"] = recommended_additional_cost
        payload["summary"]["recommended_non_local_orders"] = int(recommended_non_local_orders)
        payload["summary"]["current_local_share_percent"] = round(current_share * 100.0, 2)
        payload["summary"]["current_localization_index"] = round(float(current_index), 4)
        payload["summary"]["projected_local_share_percent"] = round(projected_share * 100.0, 2)
        payload["summary"]["projected_localization_index"] = round(float(projected_index), 4)
    else:
        payload["summary"]["total_positive_effect"] = 0.0
        payload["summary"]["localization_effect"] = 0.0
        payload["summary"]["recommended_transit_cost"] = 0.0
        payload["summary"]["recommended_extra_warehouse_cost"] = 0.0
        payload["summary"]["recommended_additional_cost"] = 0.0
        payload["summary"]["recommended_non_local_orders"] = 0
        payload["summary"]["current_local_share_percent"] = 0.0
        payload["summary"]["current_localization_index"] = 0.0
        payload["summary"]["projected_local_share_percent"] = 0.0
        payload["summary"]["projected_localization_index"] = 0.0

    payload["summary"]["selected_transit_warehouse"] = transit_warehouse or ""
    payload["summary"]["selected_main_warehouse"] = selected_main_warehouse or ""
    payload["summary"]["include_food"] = bool(include_food)
    payload["summary"]["only_with_fbs_stock"] = bool(only_with_fbs_stock)
    payload["summary"]["available_transit_warehouses"] = list_available_transit_warehouses(seller=seller)
    payload["summary"]["available_main_warehouses"] = list_regular_warehouses(seller=seller)
    payload["summary"]["base_logistics_per_order"] = round(effective_base_logistics, 2)
    if seller is not None:
        has_own_tariffs = WbWarehouseTariff.objects.filter(seller=seller).exists()
        has_own_acceptance = WbAcceptanceCoefficient.objects.filter(seller=seller).exists()
        has_own_transit = TransitDirectionTariff.objects.filter(seller=seller).exists()
        payload["summary"]["uses_shared_reference_data"] = not (
            has_own_tariffs and has_own_acceptance and has_own_transit
        )
    else:
        payload["summary"]["uses_shared_reference_data"] = True
    return payload


def _is_food_warehouse_name(name: str | None) -> bool:
    normalized = (name or "").strip().lower().replace("ё", "е")
    return "питание" in normalized
