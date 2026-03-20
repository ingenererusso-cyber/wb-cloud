from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from app.services.supply_recommendations.loaders import load_order_aggregates
from app.services.supply_recommendations.recommendations import build_supply_recommendations
from app.services.supply_recommendations.serializers import serialize_recommendations_for_dashboard
from app.services.supply_recommendations.loaders import (
    build_default_warehouse_coefficients,
    load_transit_tariffs_from_tariffs,
    load_warehouse_coefficients_from_tariffs,
)


class Command(BaseCommand):
    help = "Тестовый расчет рекомендаций поставок по регионам за период."

    def add_arguments(self, parser):
        parser.add_argument("--date-from", required=True, help="Дата начала периода в формате YYYY-MM-DD")
        parser.add_argument("--date-to", required=True, help="Дата конца периода в формате YYYY-MM-DD")
        parser.add_argument(
            "--base-logistics-per-order",
            type=float,
            default=50.0,
            help="Базовая логистика на заказ (по умолчанию: 50.0)",
        )
        parser.add_argument(
            "--penalty-factor",
            type=float,
            default=1.0,
            help="Коэффициент штрафа локализации (по умолчанию: 1.0)",
        )

    def handle(self, *args, **options):
        date_from = self._parse_date(options["date_from"], "--date-from")
        date_to = self._parse_date(options["date_to"], "--date-to")
        if date_from > date_to:
            raise CommandError("--date-from must be <= --date-to")

        base_logistics_per_order = options["base_logistics_per_order"]
        penalty_factor = options["penalty_factor"]
        if base_logistics_per_order < 0:
            raise CommandError("--base-logistics-per-order must be >= 0")
        if penalty_factor < 0:
            raise CommandError("--penalty-factor must be >= 0")

        order_aggregates = load_order_aggregates(date_from=date_from, date_to=date_to)
        if not order_aggregates:
            self.stdout.write(self.style.WARNING("За выбранный период нет данных по заказам."))
            return

        warehouse_coefficients = load_warehouse_coefficients_from_tariffs(order_aggregates)
        transit_tariffs = load_transit_tariffs_from_tariffs(order_aggregates)
        if not warehouse_coefficients:
            warehouse_coefficients = build_default_warehouse_coefficients(order_aggregates)

        results = build_supply_recommendations(
            order_aggregates=order_aggregates,
            warehouse_coefficients=warehouse_coefficients,
            transit_tariffs=transit_tariffs,
            base_logistics_per_order=base_logistics_per_order,
            penalty_factor=penalty_factor,
        )
        payload = serialize_recommendations_for_dashboard(results)

        summary = payload["summary"]
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Проверено регионов: {summary['total_regions_checked']}, "
                    f"рекомендовано: {summary['recommended_regions']}, "
                    f"суммарный плюс: {summary['total_positive_effect']:.2f}"
                )
            )
        )

        top_regions = payload["regions"][:10]
        if not top_regions:
            self.stdout.write(self.style.WARNING("Нет сценариев для вывода (проверьте коэффициенты/тарифы)."))
            return

        self.stdout.write("")
        self.stdout.write("Топ-10 регионов по net_effect:")
        self.stdout.write("region                       orders   net_effect   recommended   comment")
        self.stdout.write("-" * 90)
        for row in top_regions:
            region = (row["region_name"] or "")[:26]
            orders_count = row["orders_count"]
            net_effect = row["net_effect"]
            recommended = "yes" if row["recommended"] else "no"
            comment = (row["comment"] or "")[:38]
            self.stdout.write(
                f"{region:<26}  {orders_count:>6}  {net_effect:>11.2f}   {recommended:^11}  {comment}"
            )

    @staticmethod
    def _parse_date(raw: str, arg_name: str) -> date:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f"{arg_name} must be in format YYYY-MM-DD") from exc
