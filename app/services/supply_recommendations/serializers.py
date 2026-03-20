from __future__ import annotations

from typing import Dict, List

from app.services.supply_recommendations.models import RegionScenarioResult


def _round_money(value: float) -> float:
    return round(float(value), 2)


def serialize_recommendations_for_dashboard(results: List[RegionScenarioResult]) -> Dict:
    """Serialize recommendation results into dashboard-ready dictionary."""
    total_regions_checked = len(results)
    recommended_regions = sum(1 for item in results if item.recommended)
    total_positive_effect = _round_money(sum(item.net_effect for item in results if item.net_effect > 0))

    regions = [
        {
            "region_name": item.region_name,
            "orders_count": int(item.current_orders_count),
            "non_local_orders": int(item.non_local_orders),
            "current_total_cost": _round_money(item.current_total_cost),
            "projected_total_cost": _round_money(item.projected_total_cost),
            "projected_transit_cost": _round_money(item.projected_transit_cost),
            "projected_extra_warehouse_cost": _round_money(item.projected_extra_warehouse_cost),
            "target_warehouse_name": item.target_warehouse_name,
            "warehouse_options": [
                {
                    "warehouse_name": option.warehouse_name,
                    "transit_price_per_liter": _round_money(option.transit_price_per_liter),
                    "warehouse_coef": round(float(option.warehouse_coef), 4),
                    "projected_transit_cost": _round_money(option.projected_transit_cost),
                    "projected_extra_warehouse_cost": _round_money(option.projected_extra_warehouse_cost),
                    "total_additional_cost": _round_money(option.total_additional_cost),
                }
                for option in item.warehouse_options
            ],
            "projected_localization_savings": _round_money(item.projected_localization_savings),
            "net_effect": _round_money(item.net_effect),
            "recommended": bool(item.recommended),
            "comment": item.comment,
        }
        for item in results
    ]

    return {
        "summary": {
            "total_regions_checked": total_regions_checked,
            "recommended_regions": recommended_regions,
            "total_positive_effect": total_positive_effect,
        },
        "regions": regions,
    }
