import pytest

from app.services.supply_recommendations.calculators import (
    calculate_baseline_logistics_cost,
    calculate_local_share,
    calculate_localization_index,
    calculate_net_effect,
    calculate_non_local_orders,
    calculate_projected_local_orders,
    calculate_transit_cost,
    calculate_warehouse_logistics_cost,
)


def test_calculate_local_share():
    assert calculate_local_share(10, 4) == 0.4
    assert calculate_local_share(0, 0) == 0.0


def test_calculate_non_local_orders():
    assert calculate_non_local_orders(15, 6) == 9


def test_calculate_transit_cost():
    assert calculate_transit_cost(100, 1.5, 20.0) == 3000.0


def test_calculate_warehouse_logistics_cost():
    assert calculate_warehouse_logistics_cost(80, 45.0, 1.2) == 4320.0


def test_calculate_baseline_logistics_cost():
    assert calculate_baseline_logistics_cost(50, 30.0, 1.1) == 1650.0


def test_calculate_projected_local_orders():
    assert calculate_projected_local_orders(120, 15) == 135


def test_calculate_net_effect():
    assert calculate_net_effect(10000.0, 9200.0) == 800.0


def test_calculate_localization_index():
    assert calculate_localization_index(0.6, 1.0) == 1.4
    assert calculate_localization_index(1.0, 1.0) == 1.0


@pytest.mark.parametrize(
    "func,args",
    [
        (calculate_local_share, (-1, 0)),
        (calculate_local_share, (10, -1)),
        (calculate_non_local_orders, (5, 8)),
        (calculate_transit_cost, (-1, 1.0, 2.0)),
        (calculate_transit_cost, (1, -1.0, 2.0)),
        (calculate_transit_cost, (1, 1.0, -2.0)),
        (calculate_warehouse_logistics_cost, (10, -5.0, 1.1)),
        (calculate_baseline_logistics_cost, (10, 5.0, -1.0)),
        (calculate_projected_local_orders, (10, -1)),
        (calculate_net_effect, (-100.0, 50.0)),
        (calculate_localization_index, (-0.1, 1.0)),
        (calculate_localization_index, (1.1, 1.0)),
    ],
)
def test_validation_errors(func, args):
    with pytest.raises(ValueError):
        func(*args)
