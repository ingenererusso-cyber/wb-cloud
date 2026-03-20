import logging

from app.services.supply_recommendations.models import OrderAggregate, TransitTariff, WarehouseCoefficient
from app.services.supply_recommendations.recommendations import build_supply_recommendations
from app.services.supply_recommendations.serializers import serialize_recommendations_for_dashboard


def test_build_supply_recommendations_sorted_by_net_effect_desc():
    order_aggregates = [
        OrderAggregate(nm_id=1, supplier_article="A", order_region="Центральный", shipment_warehouse="W0", orders_count=120, avg_volume_liters=0.2),
        OrderAggregate(nm_id=2, supplier_article="B", order_region="Восток", shipment_warehouse="W0", orders_count=80, avg_volume_liters=1.6),
    ]
    warehouse_coefficients = [
        WarehouseCoefficient(warehouse_name="WC", region_name="Центральный", logistics_coef=0.7),
        WarehouseCoefficient(warehouse_name="WE", region_name="Восток", logistics_coef=1.8),
        WarehouseCoefficient(warehouse_name="W0", region_name="Северо-Западный", logistics_coef=1.0),
    ]
    transit_tariffs = [
        TransitTariff(target_region="Центральный", price_per_liter=2.0),
        TransitTariff(target_region="Восток", price_per_liter=45.0),
    ]

    results = build_supply_recommendations(
        order_aggregates=order_aggregates,
        warehouse_coefficients=warehouse_coefficients,
        transit_tariffs=transit_tariffs,
        base_logistics_per_order=50.0,
        penalty_factor=1.0,
    )

    assert len(results) == 2
    assert results[0].net_effect >= results[1].net_effect
    assert results[0].region_name == "Центральный"


def test_build_supply_recommendations_marks_region_without_tariff(caplog):
    caplog.set_level(logging.WARNING)

    order_aggregates = [
        OrderAggregate(nm_id=1, supplier_article="A", order_region="Сибирь", shipment_warehouse="W0", orders_count=50, avg_volume_liters=1.0),
    ]
    warehouse_coefficients = [
        WarehouseCoefficient(warehouse_name="WS", region_name="Сибирь", logistics_coef=1.3),
        WarehouseCoefficient(warehouse_name="W0", region_name="Центральный", logistics_coef=1.0),
    ]

    results = build_supply_recommendations(
        order_aggregates=order_aggregates,
        warehouse_coefficients=warehouse_coefficients,
        transit_tariffs=[],
        base_logistics_per_order=50.0,
    )

    assert len(results) == 1
    assert results[0].region_name == "Восток"
    assert results[0].comment == "Нет тарифа транзита для региона"
    assert results[0].net_effect == 0.0
    assert "no transit tariff" in caplog.text


def test_build_supply_recommendations_uses_baseline_when_region_without_coefficient(caplog):
    caplog.set_level(logging.WARNING)

    order_aggregates = [
        OrderAggregate(nm_id=1, supplier_article="A", order_region="Юг", shipment_warehouse="W0", orders_count=50, avg_volume_liters=1.0),
    ]
    transit_tariffs = [TransitTariff(target_region="Юг", price_per_liter=10.0)]
    warehouse_coefficients = [WarehouseCoefficient(warehouse_name="W0", region_name="Центральный", logistics_coef=1.0)]

    results = build_supply_recommendations(
        order_aggregates=order_aggregates,
        warehouse_coefficients=warehouse_coefficients,
        transit_tariffs=transit_tariffs,
        base_logistics_per_order=50.0,
        baseline_warehouse_coef=1.0,
    )

    assert len(results) == 1
    assert results[0].region_name == "Юг"
    assert "no warehouse coefficient, use baseline coefficient" in caplog.text


def test_serialize_recommendations_for_dashboard_format_and_rounding():
    order_aggregates = [
        OrderAggregate(
            nm_id=1,
            supplier_article="A",
            order_region="Центральный",
            shipment_warehouse="W0",
            orders_count=120,
            avg_volume_liters=0.2,
        ),
        OrderAggregate(
            nm_id=2,
            supplier_article="B",
            order_region="Восток",
            shipment_warehouse="W0",
            orders_count=80,
            avg_volume_liters=1.6,
        ),
    ]
    warehouse_coefficients = [
        WarehouseCoefficient(warehouse_name="WC", region_name="Центральный", logistics_coef=0.7),
        WarehouseCoefficient(warehouse_name="WE", region_name="Восток", logistics_coef=1.8),
        WarehouseCoefficient(warehouse_name="W0", region_name="Северо-Западный", logistics_coef=1.0),
    ]
    transit_tariffs = [
        TransitTariff(target_region="Центральный", price_per_liter=2.0),
        TransitTariff(target_region="Восток", price_per_liter=45.0),
    ]

    results = build_supply_recommendations(
        order_aggregates=order_aggregates,
        warehouse_coefficients=warehouse_coefficients,
        transit_tariffs=transit_tariffs,
        base_logistics_per_order=50.0,
    )
    payload = serialize_recommendations_for_dashboard(results)

    assert set(payload.keys()) == {"summary", "regions"}
    assert payload["summary"]["total_regions_checked"] == 2
    assert payload["summary"]["recommended_regions"] == 1
    assert payload["summary"]["total_positive_effect"] > 0
    assert "total_negative_effect" not in payload["summary"]
    assert len(payload["regions"]) == 2

    first = payload["regions"][0]
    assert set(first.keys()) == {
        "region_name",
        "orders_count",
        "non_local_orders",
        "current_total_cost",
        "projected_total_cost",
        "projected_transit_cost",
        "projected_extra_warehouse_cost",
        "target_warehouse_name",
        "warehouse_options",
        "projected_localization_savings",
        "net_effect",
        "recommended",
        "comment",
    }
    assert isinstance(first["current_total_cost"], float)
    assert first["current_total_cost"] == round(first["current_total_cost"], 2)
    assert isinstance(first["warehouse_options"], list)
