from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderAggregate:
    """Aggregated order metrics for one SKU/article and region/warehouse slice."""

    nm_id: int | str
    supplier_article: str
    order_region: str
    shipment_warehouse: str
    orders_count: int
    avg_volume_liters: float
    local_orders_count: int = 0


@dataclass(frozen=True)
class WarehouseCoefficient:
    """WB warehouse coefficients and region mapping used in scenario calculations."""

    warehouse_name: str
    region_name: str
    logistics_coef: float
    storage_coef: float | None = None


@dataclass(frozen=True)
class TransitTariff:
    """Transit tariff from Moscow to a target region in rubles per liter."""

    target_region: str
    price_per_liter: float
    target_warehouse_name: str | None = None


@dataclass(frozen=True)
class LocalizationMetrics:
    """Localization KPI snapshot for a selected period."""

    total_orders: int
    local_orders: int
    non_local_orders: int
    local_share: float
    localization_index: float


@dataclass(frozen=True)
class RegionScenarioInput:
    """Input parameters for simulation of supply placement in one region."""

    region_name: str
    orders_count: int
    avg_volume_liters: float
    current_local: bool
    target_warehouse_name: str
    warehouse_coef: float
    baseline_warehouse_coef: float
    transit_price_per_liter: float
    base_logistics_per_order: float
    non_local_orders_count: int = 0


@dataclass(frozen=True)
class RegionScenarioResult:
    """Calculated scenario output with economics and recommendation decision."""

    region_name: str
    current_orders_count: int
    non_local_orders: int
    current_total_cost: float
    projected_total_cost: float
    projected_transit_cost: float
    projected_extra_warehouse_cost: float
    projected_localization_savings: float
    net_effect: float
    recommended: bool
    target_warehouse_name: str
    comment: str
    warehouse_options: list["WarehouseOption"] = field(default_factory=list)


@dataclass(frozen=True)
class WarehouseOption:
    """Alternative destination warehouse for selected transit direction."""

    warehouse_name: str
    transit_price_per_liter: float
    warehouse_coef: float
    projected_transit_cost: float
    projected_extra_warehouse_cost: float
    total_additional_cost: float
