import pytest

from app.services.supply_recommendations.models import RegionScenarioInput
from app.services.supply_recommendations.scenarios import evaluate_region_scenario


def test_expensive_east_scenario_not_recommended():
    scenario_input = RegionScenarioInput(
        region_name="Восток",
        orders_count=120,
        avg_volume_liters=1.5,
        current_local=False,
        target_warehouse_name="Хабаровск",
        warehouse_coef=1.8,
        baseline_warehouse_coef=1.0,
        transit_price_per_liter=45.0,
        base_logistics_per_order=50.0,
    )

    result = evaluate_region_scenario(
        scenario_input=scenario_input,
        total_orders=1000,
        current_local_orders=300,
        current_localization_index=1.7,
        penalty_factor=1.0,
    )

    assert result.recommended is False
    assert result.net_effect < 0
    assert result.comment == "Поставка невыгодна"


def test_central_region_scenario_recommended():
    scenario_input = RegionScenarioInput(
        region_name="Центральный федеральный округ",
        orders_count=180,
        avg_volume_liters=0.2,
        current_local=False,
        target_warehouse_name="Подольск",
        warehouse_coef=0.7,
        baseline_warehouse_coef=1.0,
        transit_price_per_liter=2.0,
        base_logistics_per_order=50.0,
    )

    result = evaluate_region_scenario(
        scenario_input=scenario_input,
        total_orders=1000,
        current_local_orders=300,
        current_localization_index=1.7,
        penalty_factor=1.0,
    )

    assert result.recommended is True
    assert result.net_effect > 0
    assert result.comment == "Рекомендуется поставка"


def test_borderline_scenario_zero_effect():
    scenario_input = RegionScenarioInput(
        region_name="Центральный федеральный округ",
        orders_count=100,
        avg_volume_liters=1.0,
        current_local=True,
        target_warehouse_name="Коледино",
        warehouse_coef=1.0,
        baseline_warehouse_coef=1.0,
        transit_price_per_liter=0.0,
        base_logistics_per_order=50.0,
        non_local_orders_count=0,
    )

    result = evaluate_region_scenario(
        scenario_input=scenario_input,
        total_orders=1000,
        current_local_orders=300,
        current_localization_index=1.7,
        penalty_factor=1.0,
    )

    assert result.net_effect == pytest.approx(0.0, abs=1e-6)
    assert result.recommended is False
    assert result.comment == "Пограничный сценарий, нужна проверка"
