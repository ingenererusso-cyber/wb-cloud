import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import TesterFeedback


def _to_iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


class Command(BaseCommand):
    help = "Экспорт тикетов тестеров (TesterFeedback) в JSON/CSV."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="date_from", type=str, default=None, help="Дата начала (YYYY-MM-DD)")
        parser.add_argument("--to", dest="date_to", type=str, default=None, help="Дата конца (YYYY-MM-DD)")
        parser.add_argument("--days", type=int, default=None, help="Экспорт за последние N дней")
        parser.add_argument("--seller-id", type=int, default=None, help="Фильтр по SellerAccount.id")
        parser.add_argument("--status", type=str, default=None, help="Фильтр по статусу тикета")
        parser.add_argument("--category", type=str, default=None, help="Фильтр по категории тикета")
        parser.add_argument("--format", choices=["json", "csv"], default="json", help="Формат экспорта")
        parser.add_argument("--out", type=str, default=None, help="Путь выходного файла")

    def handle(self, *args, **options):
        date_from_raw = options["date_from"]
        date_to_raw = options["date_to"]
        days = options["days"]
        seller_id = options["seller_id"]
        status = options["status"]
        category = options["category"]
        export_format = options["format"]
        out_path_raw = options["out"]

        qs = TesterFeedback.objects.select_related("user", "seller").order_by("-created_at")

        if days is not None:
            if days < 0:
                raise CommandError("--days не может быть отрицательным")
            since = timezone.now() - timedelta(days=days)
            qs = qs.filter(created_at__gte=since)
        else:
            if date_from_raw:
                try:
                    date_from = date.fromisoformat(date_from_raw)
                except ValueError as exc:
                    raise CommandError(f"Некорректная --from: {exc}") from exc
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to_raw:
                try:
                    date_to = date.fromisoformat(date_to_raw)
                except ValueError as exc:
                    raise CommandError(f"Некорректная --to: {exc}") from exc
                qs = qs.filter(created_at__date__lte=date_to)

        if seller_id is not None:
            qs = qs.filter(seller_id=seller_id)
        if status:
            qs = qs.filter(status=status)
        if category:
            qs = qs.filter(category=category)

        rows = []
        for item in qs:
            rows.append(
                {
                    "id": item.id,
                    "created_at": _to_iso(item.created_at),
                    "updated_at": _to_iso(item.updated_at),
                    "resolved_at": _to_iso(item.resolved_at),
                    "user_id": item.user_id,
                    "username": item.user.username if item.user else "",
                    "seller_id": item.seller_id,
                    "seller_name": item.seller.name if item.seller else "",
                    "page_url": item.page_url,
                    "category": item.category,
                    "priority": item.priority,
                    "status": item.status,
                    "include_context": item.include_context,
                    "message": item.message,
                    "context_json": item.context_json or {},
                }
            )

        if not out_path_raw:
            stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
            out_path_raw = f"exports/feedback_{stamp}.{export_format}"
        out_path = Path(out_path_raw)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if export_format == "json":
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
        else:
            fieldnames = [
                "id",
                "created_at",
                "updated_at",
                "resolved_at",
                "user_id",
                "username",
                "seller_id",
                "seller_name",
                "page_url",
                "category",
                "priority",
                "status",
                "include_context",
                "message",
                "context_json",
            ]
            with out_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    row_out = dict(row)
                    row_out["context_json"] = json.dumps(row_out["context_json"], ensure_ascii=False)
                    writer.writerow(row_out)

        self.stdout.write(self.style.SUCCESS(f"Экспортировано тикетов: {len(rows)}"))
        self.stdout.write(self.style.SUCCESS(f"Файл: {out_path}"))
