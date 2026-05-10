## Sync Queue Processes

This project now supports a separated sync architecture:

- `web`: Django app (`mp_saas.service`)
- `worker`: executes queued heavy sync tasks
- `scheduler`: periodically enqueues due auto-sync jobs

### Suggested processes

Worker:

```bash
/var/www/mp_saas/venv/bin/python3 manage.py run_sync_worker --poll-seconds 2
```

Scheduler:

```bash
/var/www/mp_saas/venv/bin/python3 manage.py run_sync_scheduler --poll-seconds 60
```

### Deployment notes

- Run at least one worker in production before enabling queue-based sync.
- Start with one worker process on small servers.
- Only increase worker concurrency after checking PostgreSQL RAM/CPU usage.
- Scheduler should run as a separate process or service, not from web requests.
