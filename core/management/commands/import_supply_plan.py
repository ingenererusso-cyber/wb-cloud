import datetime
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from core.models import PricingPolicy, Product, SellerAccount
from core.price_robot import default_target_zero_date


class Command(BaseCommand):
    help = (
        "Импорт плана закупок (xlsx) в PricingPolicy: входящее кол-во и закупочная "
        "цена по артикулам. Лист(ы) 'Поставка N' с колонками Артикул / Кол-во, шт / Цена/шт, ₽."
    )

    def add_arguments(self, parser):
        parser.add_argument("xlsx_path", type=str, help="Путь к файлу плана закупок.")
        parser.add_argument("--seller", type=int, required=True, help="ID продавца.")
        parser.add_argument(
            "--target-zero-date",
            type=str,
            default=None,
            help="Дедлайн выхода в 0 (YYYY-MM-DD). По умолчанию — ближайшее 15 фев.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Только показать, не записывать.")

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError as exc:
            raise CommandError("Нужен openpyxl (он есть в requirements).") from exc

        try:
            seller = SellerAccount.objects.get(id=options["seller"])
        except SellerAccount.DoesNotExist as exc:
            raise CommandError(f"Продавец id={options['seller']} не найден.") from exc

        target_zero = (
            datetime.date.fromisoformat(options["target_zero_date"])
            if options.get("target_zero_date")
            else default_target_zero_date(datetime.date.today())
        )

        wb = openpyxl.load_workbook(options["xlsx_path"], read_only=True, data_only=True)

        # article -> {"qty": int, "price": float|None, "title": str}
        agg: dict[str, dict] = defaultdict(lambda: {"qty": 0, "price": None, "title": ""})

        for ws in wb.worksheets:
            if not ws.title.lower().startswith("поставка"):
                continue
            header = None
            col = {}
            for row in ws.iter_rows(values_only=True):
                if header is None:
                    header = [str(c).strip().lower() if c is not None else "" for c in row]
                    for i, name in enumerate(header):
                        if name.startswith("артикул"):
                            col["article"] = i
                        elif name.startswith("кол-во"):
                            col["qty"] = i
                        elif name.startswith("цена"):
                            col["price"] = i
                        elif name.startswith("название"):
                            col["title"] = i
                    continue
                if "article" not in col or "qty" not in col:
                    break
                article = row[col["article"]] if col["article"] < len(row) else None
                if not article:
                    continue
                article = str(article).strip()
                qty = row[col["qty"]] if col.get("qty", -1) < len(row) else None
                price = row[col["price"]] if "price" in col and col["price"] < len(row) else None
                title = row[col["title"]] if "title" in col and col["title"] < len(row) else None
                try:
                    qty = int(float(qty)) if qty is not None else 0
                except (TypeError, ValueError):
                    qty = 0
                agg[article]["qty"] += qty
                if price is not None and agg[article]["price"] is None:
                    try:
                        agg[article]["price"] = float(price)
                    except (TypeError, ValueError):
                        pass
                if title and not agg[article]["title"]:
                    agg[article]["title"] = str(title).strip()

        if not agg:
            self.stdout.write(self.style.WARNING("В файле не найдено строк на листах 'Поставка N'."))
            return

        vendor_to_nm = {
            (vc or "").strip(): int(nm)
            for vc, nm in Product.objects.filter(seller=seller).values_list("vendor_code", "nm_id")
            if vc
        }

        matched, unmatched = 0, []
        for article, data in sorted(agg.items()):
            nm_id = vendor_to_nm.get(article)
            if nm_id is None:
                unmatched.append(article)
                continue
            matched += 1
            if options.get("dry_run"):
                self.stdout.write(
                    f"  {article} → nm {nm_id}: +{data['qty']} шт, закуп {data['price']}"
                )
                continue
            PricingPolicy.objects.update_or_create(
                seller=seller,
                nm_id=nm_id,
                defaults={
                    "vendor_code": article,
                    "incoming_qty": data["qty"],
                    "purchase_price": data["price"],
                    "target_zero_date": target_zero,
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f"Артикулов в плане: {len(agg)}. Сопоставлено с товарами: {matched}. "
            f"Не найдено в карточках: {len(unmatched)}."
        ))
        if unmatched:
            self.stdout.write("Не сопоставлены (нет такого vendor_code в товарах): " + ", ".join(unmatched[:40]))
        if not options.get("dry_run"):
            self.stdout.write(self.style.SUCCESS(f"Дедлайн выхода в 0 записан: {target_zero.isoformat()}"))
