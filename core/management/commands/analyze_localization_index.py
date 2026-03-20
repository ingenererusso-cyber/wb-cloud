from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.models import SellerAccount
from core.services_realization import calculate_fact_vs_theory_localization_index


class Command(BaseCommand):
    help = "Считает фактический индекс локализации как fact/theory по данным отчета реализации."

    def add_arguments(self, parser):
        parser.add_argument("--date-from", required=True, help="YYYY-MM-DD")
        parser.add_argument("--date-to", required=True, help="YYYY-MM-DD")
        parser.add_argument("--seller-id", type=int, required=False, help="ID SellerAccount")
        parser.add_argument("--all-bonus-types", action="store_true", help="Не ограничивать только логистикой 'К клиенту'")
        parser.add_argument("--include-cancel", action="store_true", help="Включать строки логистики не только 'при продаже'")

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

        result = calculate_fact_vs_theory_localization_index(
            seller=seller,
            date_from=date_from,
            date_to=date_to,
            include_only_to_client_logistics=not options["all_bonus_types"],
            include_only_sale_rows=not options["include_cancel"],
        )
        self.stdout.write(self.style.SUCCESS(f"fact/theory result: {result}"))

    @staticmethod
    def _parse_date(raw: str, arg_name: str) -> date:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f"{arg_name} must be YYYY-MM-DD") from exc
