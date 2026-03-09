from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Order, SellerAccount
from core.services.localization import determine_locality


class Command(BaseCommand):
    help = "Пересчитать поле is_local у заказов по актуальной логике локализации."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seller-id",
            type=int,
            default=None,
            help="ID продавца. Если не указан, пересчёт для всех продавцов.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Пересчитать только за последние N дней.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=1000,
            help="Размер чанка для bulk_update.",
        )

    def handle(self, *args, **options):
        seller_id = options["seller_id"]
        days = options["days"]
        chunk_size = options["chunk_size"]

        sellers_qs = SellerAccount.objects.all()
        if seller_id is not None:
            sellers_qs = sellers_qs.filter(id=seller_id)

        sellers = list(sellers_qs)
        if not sellers:
            self.stdout.write(self.style.WARNING("Продавцы не найдены."))
            return

        date_from = None
        if days:
            date_from = timezone.now() - timedelta(days=days)

        total_checked = 0
        total_updated = 0

        for seller in sellers:
            orders_qs = Order.objects.filter(seller=seller, warehouse_type="Склад WB")
            if date_from:
                orders_qs = orders_qs.filter(order_date__gte=date_from)

            checked = 0
            updated = 0
            buffer = []

            for order in orders_qs.only("id", "warehouse_name", "oblast_okrug_name", "is_local").iterator(
                chunk_size=chunk_size
            ):
                checked += 1
                actual_is_local = determine_locality(order.warehouse_name, order.oblast_okrug_name)
                if order.is_local != actual_is_local:
                    order.is_local = actual_is_local
                    buffer.append(order)

                if len(buffer) >= chunk_size:
                    Order.objects.bulk_update(buffer, ["is_local"], batch_size=chunk_size)
                    updated += len(buffer)
                    buffer.clear()

            if buffer:
                Order.objects.bulk_update(buffer, ["is_local"], batch_size=chunk_size)
                updated += len(buffer)

            total_checked += checked
            total_updated += updated

            self.stdout.write(
                f"Seller {seller.id} ({seller.name}): проверено {checked}, обновлено {updated}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Всего проверено {total_checked}, обновлено {total_updated}"
            )
        )
