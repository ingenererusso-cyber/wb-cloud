from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.models import SellerAccount
from core.services_realization import sync_realization_report_detail
from core.services_tariffs import sync_warehouse_tariffs_for_period


class Command(BaseCommand):
    help = "Синхронизирует детализацию отчета реализации WB в БД."

    def add_arguments(self, parser):
        parser.add_argument("--date-from", required=True, help="YYYY-MM-DD")
        parser.add_argument("--date-to", required=True, help="YYYY-MM-DD")
        parser.add_argument("--seller-id", type=int, required=False, help="ID SellerAccount")
        parser.add_argument("--period", default="weekly", choices=["weekly", "daily"])
        parser.add_argument("--no-rate-limit", action="store_true", help="Не ждать 61 секунду между страницами")
        parser.add_argument(
            "--sync-tariffs-history",
            action="store_true",
            help="Дополнительно синхронизировать tariffs/box за этот же период",
        )

    def handle(self, *args, **options):
        date_from = self._parse_date(options["date_from"], "--date-from")
        date_to = self._parse_date(options["date_to"], "--date-to")
        if date_from > date_to:
            raise CommandError("--date-from must be <= --date-to")

        seller_id = options.get("seller_id")
        if seller_id:
            seller = SellerAccount.objects.filter(id=seller_id).first()
        else:
            seller = SellerAccount.objects.exclude(api_token="").order_by("-id").first()
        if not seller:
            raise CommandError("SellerAccount not found")

        self.stdout.write(f"seller_id={seller.id} seller_name={seller.name}")

        if options["sync_tariffs_history"]:
            synced_tariffs = sync_warehouse_tariffs_for_period(
                seller=seller,
                date_from=date_from,
                date_to=date_to,
            )
            self.stdout.write(self.style.SUCCESS(f"synced historical tariffs rows={synced_tariffs}"))

        result = sync_realization_report_detail(
            seller=seller,
            date_from=date_from,
            date_to=date_to,
            period=options["period"],
            respect_rate_limit=not options["no_rate_limit"],
        )
        self.stdout.write(self.style.SUCCESS(f"realization sync result: {result}"))

    @staticmethod
    def _parse_date(raw: str, arg_name: str) -> date:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f"{arg_name} must be YYYY-MM-DD") from exc
