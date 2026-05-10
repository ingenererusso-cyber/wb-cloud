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

These commands are foreground processes, so they will occupy the terminal until stopped.
For local debugging use another terminal, `tmux`, or background execution.
For production use separate `systemd` services.

## Run together with `mp_saas.service`

The provided units are configured with:

- `PartOf=mp_saas.service`
- `WantedBy=mp_saas.service`

So they can be started and stopped together with the main app service.

### Install on server

```bash
sudo cp deploy/systemd/mp_saas_sync_worker.service /etc/systemd/system/
sudo cp deploy/systemd/mp_saas_sync_scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mp_saas_sync_worker.service
sudo systemctl enable mp_saas_sync_scheduler.service
```

### Start or restart together

```bash
sudo systemctl restart mp_saas
sudo systemctl restart mp_saas_sync_worker
sudo systemctl restart mp_saas_sync_scheduler
```

### Check status

```bash
sudo systemctl status mp_saas --no-pager -l
sudo systemctl status mp_saas_sync_worker --no-pager -l
sudo systemctl status mp_saas_sync_scheduler --no-pager -l
```

### Deployment notes

- Run at least one worker in production before enabling queue-based sync.
- Start with one worker process on small servers.
- Only increase worker concurrency after checking PostgreSQL RAM/CPU usage.
- Scheduler should run as a separate process or service, not from web requests.
