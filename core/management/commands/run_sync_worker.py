import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.views import (
    _claim_next_queued_sync_task,
    _execute_sync_task,
    _expire_stale_running_sync_tasks,
)


class Command(BaseCommand):
    help = "Runs background sync worker that processes queued SyncTask records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=2.0,
            help="How often to poll the DB queue when there are no tasks.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued task and exit.",
        )

    def handle(self, *args, **options):
        poll_seconds = max(0.5, float(options["poll_seconds"] or 2.0))
        run_once = bool(options["once"])

        self.stdout.write(self.style.SUCCESS("Sync worker started."))

        while True:
            close_old_connections()
            _expire_stale_running_sync_tasks()
            task = _claim_next_queued_sync_task()
            if not task:
                if run_once:
                    self.stdout.write("No queued sync tasks.")
                    return
                time.sleep(poll_seconds)
                continue

            self.stdout.write(
                f"Processing sync task {task.task_id} "
                f"(kind={task.kind}, user_id={task.user_id}, seller_id={task.seller_id})"
            )
            try:
                _execute_sync_task(task)
            finally:
                close_old_connections()

            if run_once:
                return
