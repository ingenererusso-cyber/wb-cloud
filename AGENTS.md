# Agent Notes

Этот файл — короткая практическая памятка для новых агентов по проекту `mp_saas`.
Он дополняет [DESIGN.md](/Users/nikita/Documents/mp_saas/DESIGN.md) и фиксирует именно те нюансы, на которых легко ошибиться при разработке, отладке и локальном запуске.

Если этот файл и реальный код расходятся:
- сначала доверяй рабочему коду;
- затем обнови этот файл вместе с исправлением.


## 1. Главное про sync

- В проекте нет Celery/RQ для общей синхронизации.
- Общий sync работает через DB queue на модели `SyncTask`.
- Нажатие кнопки sync в UI не выполняет синк сразу: оно только создаёт запись со статусом `queued`.
- Реальное выполнение делает отдельный worker:
  - `./venv/bin/python manage.py run_sync_worker`
- Плановый auto-sync ставит задачи в очередь отдельным scheduler-процессом:
  - `./venv/bin/python manage.py run_sync_scheduler`

Что это значит practically:
- если задача “висит в очереди”, почти всегда не запущен worker;
- если web работает, это не значит, что sync работает;
- для локальной разработки нужно держать минимум 2 процесса:
  - `runserver`
  - `run_sync_worker`


## 2. Локальная разработка на macOS

- Локально проект обычно работает не через `systemd`, а через отдельные терминалы.
- Для проверки очереди удобно:
  - `./venv/bin/python manage.py run_sync_worker --once`
- Для постоянной локальной обработки очереди:
  - `./venv/bin/python manage.py run_sync_worker --poll-seconds 2`

Важно:
- local web и local worker должны смотреть в одну и ту же БД;
- в `.env` сейчас может быть `DB_ENGINE=postgres`, даже если рядом лежит `db.sqlite3`;
- не делай вывод “проект на sqlite” только потому, что файл `db.sqlite3` есть в репозитории.


## 3. Нюанс с тестами и локальной Postgres

- `manage.py check` может проходить, а `manage.py test` — нет.
- Типичная локальная причина: у пользователя Postgres нет права `CREATE DATABASE` для test DB.
- Если `manage.py test` падает на создании тестовой базы, это не обязательно проблема кода.
- В таких случаях:
  - по возможности используй точечную проверку через `manage.py shell`;
  - явно сообщай пользователю, что полноценный test run упёрся в права БД.


## 4. Нюанс FBO-остатков

- FBO-остатки хранятся в `WarehouseStockDetailed`.
- Экран FBO берёт строки с `quantity > 0`.
- Ключевая ловушка: sync FBO должен не только обновлять/создавать строки, но и удалять устаревшие строки, которых больше нет в ответе WB.
- Иначе товар может “залипнуть” в интерфейсе со старым количеством даже после актуального sync.

Текущая ожидаемая логика:
- `sync_supplier_stocks(...)` делает upsert актуальных строк;
- затем удаляет stale-строки, отсутствующие в последнем payload WB.

Если пользователь говорит:
- “на WB остатка уже нет, а у нас всё ещё показывается”,
проверь сначала именно этот путь.


## 5. Не смешивать FBO и FBS

- `WarehouseStockDetailed` — это FBO / складские остатки WB.
- `SellerFbsStock` — это FBS-остатки продавца.
- Эти потоки sync и их UI нельзя мысленно объединять.
- При отладке обязательно уточняй:
  - проблема в FBO,
  - или в FBS,
  - или в отображении объединённого отчёта.


## 6. Где искать правду по остаткам

Для FBO:
- sync: [core/services_stocks.py](/Users/nikita/Documents/mp_saas/core/services_stocks.py)
- отчёты/экраны используют `WarehouseStockDetailed` из [core/views.py](/Users/nikita/Documents/mp_saas/core/views.py)

Для FBS:
- sync: [core/services_fbs_stocks.py](/Users/nikita/Documents/mp_saas/core/services_fbs_stocks.py)
- данные хранятся в `SellerFbsStock`

Для общего sync:
- постановка задачи в очередь: [core/views.py](/Users/nikita/Documents/mp_saas/core/views.py)
- worker: [core/management/commands/run_sync_worker.py](/Users/nikita/Documents/mp_saas/core/management/commands/run_sync_worker.py)
- scheduler: [core/management/commands/run_sync_scheduler.py](/Users/nikita/Documents/mp_saas/core/management/commands/run_sync_scheduler.py)


## 7. Как интерпретировать статусы SyncTask

- `queued` — задача только поставлена в очередь, ещё не выполняется
- `running` — worker уже забрал её
- `success` — завершена
- `error` — завершилась ошибкой или была автоматически помечена stale
- `canceled` — остановлена вручную

Если видишь:
- `queued > 0`
- `running = 0`

то сначала подозревай отсутствие worker-процесса, а не баг в бизнес-логике sync.


## 8. Что проверять перед выводом “sync сломан”

1. Есть ли `SyncTask` в `queued` / `running`.
2. Запущен ли worker.
3. Обновляется ли `updated_at` у task.
4. Не stale ли задача.
5. Это проблема sync-логики или stale-данных в БД.
6. Это FBO или FBS.


## 9. Практическое правило для будущих правок

Если синхронизация читает “полный срез” сущности из WB API, агент должен проверять не только:
- как создать новые записи;
- как обновить существующие;

но и:
- как убрать записи, которые исчезли из внешнего источника.

Это особенно важно для:
- остатков;
- списков складов;
- связанных сущностей, которые UI показывает как текущее состояние.


## 10. Когда обновлять этот файл

Обновляй `AGENTS.md`, если обнаружился любой из типов нюансов:
- локальный operational gotcha;
- расхождение между “как кажется” и “как реально работает проект”;
- скрытая зависимость между web и background process;
- stale-data bug, который легко повторно внести;
- ограничение локальной инфраструктуры, влияющее на отладку или тесты.
