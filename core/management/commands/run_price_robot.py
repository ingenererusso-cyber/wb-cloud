import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import PriceRobotRun, SellerAccount
from core.price_robot import apply_plan, build_plan, default_target_zero_date


class Command(BaseCommand):
    help = (
        "Ценовой робот: считает план цен (dry-run) и, при --apply, пишет их в WB. "
        "По умолчанию только считает и сохраняет PriceRobotRun, ничего не отправляя."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--seller",
            type=int,
            default=None,
            help="ID продавца. По умолчанию — все продавцы с API-ключом.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Отправить рассчитанные цены в WB (иначе только план/dry-run).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Обойти кулдаун «раз в сутки на артикул» при --apply (для отладки).",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Переопределить 'сегодня' (YYYY-MM-DD) для теста сезонной логики.",
        )
        parser.add_argument(
            "--target-zero-date",
            type=str,
            default=None,
            help="Переопределить дедлайн выхода в 0 (YYYY-MM-DD).",
        )

    def handle(self, *args, **options):
        today = None
        if options.get("date"):
            today = datetime.date.fromisoformat(options["date"])
        target_zero = None
        if options.get("target_zero_date"):
            target_zero = datetime.date.fromisoformat(options["target_zero_date"])

        do_apply = bool(options.get("apply"))
        force = bool(options.get("force"))

        sellers = self._select_sellers(options.get("seller"))
        if not sellers:
            self.stdout.write(self.style.WARNING("Нет подходящих продавцов."))
            return

        for seller in sellers:
            self._run_for_seller(seller, today=today, target_zero=target_zero, do_apply=do_apply, force=force)

    def _select_sellers(self, seller_id):
        if seller_id:
            return list(SellerAccount.objects.filter(id=seller_id))
        return [s for s in SellerAccount.objects.all() if s.has_api_token]

    def _run_for_seller(self, seller, *, today, target_zero, do_apply, force=False):
        eff_target = target_zero or default_target_zero_date(today or timezone.localdate())
        run = PriceRobotRun.objects.create(
            seller=seller,
            mode=PriceRobotRun.MODE_APPLY if do_apply else PriceRobotRun.MODE_PLAN,
            status=PriceRobotRun.STATUS_RUNNING,
        )
        try:
            plan = build_plan(seller, today=today, target_zero_date=eff_target)
            run.season_phase = plan.get("phase", "")
            run.summary = plan.get("summary", {})
            run.result = plan

            applied_info = None
            if do_apply:
                if not seller.has_api_token:
                    raise RuntimeError("У продавца нет API-ключа — применение цен невозможно.")
                applied_info = apply_plan(seller, plan.get("decisions", []), force=force, run=run)
                run.summary = {
                    **run.summary,
                    "applied_sent": applied_info.get("sent", 0),
                    "skipped_cooldown": applied_info.get("skipped_cooldown", 0),
                }

            run.status = PriceRobotRun.STATUS_SUCCESS
            run.finished_at = timezone.now()
            run.save()

            s = run.summary
            self.stdout.write(self.style.SUCCESS(
                f"[{seller.name}] {plan['phase_label']} | дедлайн {plan['target_zero_date']} "
                f"(осталось {plan['days_left']} дн). "
                f"SKU {s.get('total_sku', 0)}: ↑{s.get('raise', 0)} ↓{s.get('lower', 0)} "
                f"={s.get('hold', 0)} без остатка {s.get('no_stock', 0)}."
            ))
            if do_apply and applied_info is not None:
                self.stdout.write(self.style.SUCCESS(
                    f"[{seller.name}] Отправлено в WB: {applied_info.get('sent', 0)} позиций; "
                    f"пропущено по кулдауну (раз в сутки на артикул): {applied_info.get('skipped_cooldown', 0)}."
                ))
        except Exception as exc:
            run.status = PriceRobotRun.STATUS_ERROR
            run.error = str(exc)
            run.finished_at = timezone.now()
            run.save()
            self.stdout.write(self.style.ERROR(f"[{seller.name}] Ошибка ценового робота: {exc}"))
