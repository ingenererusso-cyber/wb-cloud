import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.models import SellerAccount
from core.views import _maybe_start_scheduled_sync_for_user


class Command(BaseCommand):
    help = "Queues due auto-sync jobs for sellers with enabled schedule."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=60.0,
            help="How often to check schedules when running continuously.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one scheduling pass and exit.",
        )

    def handle(self, *args, **options):
        poll_seconds = max(5.0, float(options["poll_seconds"] or 60.0))
        run_once = bool(options["once"])

        self.stdout.write(self.style.SUCCESS("Sync scheduler started."))

        while True:
            close_old_connections()
            queued_count = 0
            sellers = (
                SellerAccount.objects
                .select_related("user")
                .exclude(user__isnull=True)
                .exclude(api_token="")
            )
            for seller in sellers.iterator():
                before_meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
                before_last_run_date = (
                    ((before_meta.get("auto_sync") or {}).get("last_run_date"))
                    if isinstance(before_meta.get("auto_sync"), dict)
                    else ""
                )
                _maybe_start_scheduled_sync_for_user(seller.user, seller)
                seller.refresh_from_db(fields=["sync_meta"])
                after_meta = seller.sync_meta if isinstance(seller.sync_meta, dict) else {}
                after_last_run_date = (
                    ((after_meta.get("auto_sync") or {}).get("last_run_date"))
                    if isinstance(after_meta.get("auto_sync"), dict)
                    else ""
                )
                if after_last_run_date and after_last_run_date != before_last_run_date:
                    queued_count += 1

            self.stdout.write(f"Scheduling pass finished. Queued tasks: {queued_count}.")
            if run_once:
                return
            time.sleep(poll_seconds)
