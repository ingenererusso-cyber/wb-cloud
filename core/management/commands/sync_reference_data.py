from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import SellerAccount
from core.services_tariffs import (
    sync_acceptance_coefficients,
    sync_transit_direction_tariffs,
    sync_warehouse_tariffs,
    sync_warehouse_tariffs_for_period,
)


class Command(BaseCommand):
    help = (
        "Синхронизирует справочные данные WB (тарифы коробов, коэффициенты приёмки, "
        "транзитные направления) для указанного seller. "
        "Эти данные используются как fallback в рекомендациях для пользователей без API-ключа."
    )

    def add_arguments(self, parser):
        parser.add_argument("--seller-id", type=int, required=False, help="ID SellerAccount")
        parser.add_argument(
            "--history-days",
            type=int,
            default=0,
            help="Если > 0, дополнительно подтянуть историю тарифов коробов за N дней",
        )

    def handle(self, *args, **options):
        seller_id = options.get("seller_id")
        history_days = int(options.get("history_days") or 0)

        if seller_id:
            seller = SellerAccount.objects.filter(id=seller_id).first()
        else:
            seller = (
                SellerAccount.objects
                .exclude(api_token="")
                .exclude(api_token__isnull=True)
                .order_by("-id")
                .first()
            )

        if not seller:
            raise CommandError("SellerAccount с API-ключом не найден")

        self.stdout.write(self.style.NOTICE(f"Используется seller: {seller.id} ({seller.name})"))

        tariffs_count = sync_warehouse_tariffs(seller=seller)
        acceptance_count = sync_acceptance_coefficients(seller=seller)
        transit_count = sync_transit_direction_tariffs(seller=seller)

        history_count = 0
        if history_days > 0:
            today = timezone.localdate()
            date_from = today - timedelta(days=history_days)
            history_count = sync_warehouse_tariffs_for_period(
                seller=seller,
                date_from=date_from,
                date_to=today,
            )

        self.stdout.write(self.style.SUCCESS("Синхронизация справочников завершена"))
        self.stdout.write(f"- Тарифы коробов (сегодня): {tariffs_count}")
        if history_days > 0:
            self.stdout.write(f"- Тарифы коробов (история за {history_days} дн): {history_count}")
        self.stdout.write(f"- Коэффициенты приёмки: {acceptance_count}")
        self.stdout.write(f"- Транзитные направления: {transit_count}")
        self.stdout.write(
            "Примечание: эти данные будут использоваться как fallback "
            "для пользователей без собственных синхронизированных справочников."
        )
