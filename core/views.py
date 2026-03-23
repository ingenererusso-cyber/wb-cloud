from datetime import date, timedelta
import json
import threading
import traceback
import uuid

from django.contrib.auth import logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.db.models import Min
from django.db import close_old_connections
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from app.services.supply_recommendations.loaders import list_available_transit_warehouses, list_regular_warehouses
from app.services.supply_recommendations.service import get_dashboard_supply_recommendations
from core.models import (
    AppErrorLog,
    Order,
    Product,
    RealizationReportDetail,
    SellerAccount,
    SyncTask,
    TesterFeedback,
    TransitDirectionTariff,
    WarehouseStockDetailed,
    WbAcceptanceCoefficient,
    WbWarehouseTariff,
)
from core.services.replenishment import calculate_replenishment
from core.services_realization import (
    get_fact_localization_index_trend_last_full_weeks,
    sync_realization_report_detail,
)
from core.services_offices import sync_wb_offices
from core.services_excel_import import import_orders_from_excel
from core.services_orders import sync_fbw_orders
from core.services_products import sync_products_content
from core.services_stocks import sync_supplier_stocks
from core.services_tariffs import (
    sync_acceptance_coefficients,
    sync_transit_direction_tariffs,
    sync_warehouse_tariffs,
)
from core.services.localization import (
    get_local_orders_percent_last_full_week,
    get_local_orders_percent_trend_last_full_weeks,
    get_theoretical_localization_index_trend_last_full_weeks,
    get_top_non_local_districts_last_full_weeks,
)

def _get_seller_for_user(user):
    try:
        return user.seller_account
    except SellerAccount.DoesNotExist:
        return None


def _get_or_create_seller_for_user(user):
    seller = _get_seller_for_user(user)
    if seller:
        return seller
    display_name = user.get_full_name().strip() or user.username
    seller, _ = SellerAccount.objects.get_or_create(
        user=user,
        defaults={"name": display_name, "api_token": ""},
    )
    return seller


def _set_sync_task(task_id: str, payload: dict) -> None:
    user_id = payload.pop("user_id", None)
    seller_id = payload.pop("seller_id", None)
    defaults = {
        "status": payload.get("status") or SyncTask.STATUS_RUNNING,
        "progress": int(payload.get("progress") or 0),
        "step": payload.get("step") or "",
        "message": payload.get("message") or "",
        "result": payload.get("result") or {},
        "finished_at": payload.get("finished_at"),
    }
    if user_id is not None:
        defaults["user_id"] = user_id
    if seller_id is not None:
        defaults["seller_id"] = seller_id
    SyncTask.objects.update_or_create(task_id=task_id, defaults=defaults)


def _get_sync_task(task_id: str) -> SyncTask | None:
    return SyncTask.objects.filter(task_id=task_id).first()


def _normalize_name_for_match(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _resolve_name_case_insensitive(raw_value: str | None, options: list[str]) -> str | None:
    normalized = _normalize_name_for_match(raw_value)
    if not normalized:
        return None
    for option in options:
        if _normalize_name_for_match(option) == normalized:
            return option
    return (raw_value or "").strip() or None


def _get_last_sync_at_for_user(user, seller=None):
    last_sync_task = (
        SyncTask.objects
        .filter(user=user, status=SyncTask.STATUS_SUCCESS, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    if last_sync_task:
        return last_sync_task.finished_at

    if not seller:
        return None

    # Fallback: если SyncTask не заполнялся, берём самую свежую дату обновления данных продавца.
    candidates = []
    candidates.append(
        Order.objects.filter(seller=seller).aggregate(max_dt=Max("created_at")).get("max_dt")
    )
    candidates.append(
        WarehouseStockDetailed.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        WbWarehouseTariff.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        WbAcceptanceCoefficient.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        TransitDirectionTariff.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )
    candidates.append(
        RealizationReportDetail.objects.filter(seller=seller).aggregate(max_dt=Max("updated_at")).get("max_dt")
    )

    dates = [dt for dt in candidates if dt is not None]
    return max(dates) if dates else None


def _log_app_error(
    *,
    source: str,
    message: str,
    user=None,
    seller=None,
    path: str = "",
    context: dict | None = None,
    traceback_text: str = "",
) -> None:
    try:
        AppErrorLog.objects.create(
            source=source,
            message=message,
            user=user,
            seller=seller,
            path=path or "",
            context_json=context or {},
            traceback_text=traceback_text or "",
        )
    except Exception:
        pass


def _run_sync_orders_task(task_id: str, seller_id: int, user_id: int) -> None:
    close_old_connections()
    try:
        seller = SellerAccount.objects.filter(id=seller_id, user_id=user_id).first()
        if not seller:
            _set_sync_task(
                task_id,
                {
                    "task_id": task_id,
                    "status": "error",
                    "progress": 0,
                    "message": "SellerAccount не найден для текущего пользователя.",
                    "finished_at": timezone.now(),
                    "result": {},
                },
            )
            return

        result = {}
        steps = [
            ("Карточки товаров", "products", sync_products_content, {"seller": seller}),
            ("Тарифы коробов", "tariffs", sync_warehouse_tariffs, {"seller": seller}),
            ("Тарифы приёмки", "acceptance", sync_acceptance_coefficients, {"seller": seller}),
            ("Склады WB", "offices", sync_wb_offices, {"seller": seller}),
            ("Транзитные направления", "transit", sync_transit_direction_tariffs, {"seller": seller}),
            ("Заказы", "orders", sync_fbw_orders, {"seller": seller, "days_back": 175}),
            ("Остатки", "stocks", sync_supplier_stocks, {"seller": seller}),
        ]

        total_steps = len(steps) + 1  # + отчеты реализации
        for idx, (label, key, fn, kwargs) in enumerate(steps, start=1):
            _set_sync_task(
                task_id,
                {
                    "task_id": task_id,
                    "status": "running",
                    "progress": int(((idx - 1) / total_steps) * 100),
                    "step": label,
                    "message": f"Шаг {idx}/{total_steps}: {label}...",
                    "finished_at": None,
                    "result": result,
                },
            )
            result[key] = int(fn(**kwargs) or 0)

        realization_warning = None
        idx = total_steps
        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "running",
                "progress": int(((idx - 1) / total_steps) * 100),
                "step": "Отчёты реализации",
                "message": f"Шаг {idx}/{total_steps}: Отчёты реализации...",
                    "finished_at": None,
                    "result": result,
                },
            )
        try:
            today = timezone.localdate()
            realization_result = sync_realization_report_detail(
                seller=seller,
                date_from=today - timedelta(days=175),
                date_to=today,
                period="weekly",
                respect_rate_limit=False,
            )
            result["realization_rows"] = int(realization_result.get("upserted_rows") or 0)
        except Exception as exc:
            result["realization_rows"] = 0
            realization_warning = str(exc)

        message = (
            f"Синхронизация завершена: карточек {result.get('products', 0)}, "
            f"тарифов коробов {result.get('tariffs', 0)}, "
            f"тарифов приёмки {result.get('acceptance', 0)}, "
            f"транзитных направлений {result.get('transit', 0)}, "
            f"складов {result.get('offices', 0)}, заказов {result.get('orders', 0)}, "
            f"остатков {result.get('stocks', 0)}, "
            f"строк отчёта реализации {result.get('realization_rows', 0)}."
        )
        if realization_warning:
            message += f" Отчёты реализации частично пропущены: {realization_warning}"

        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "success",
                "progress": 100,
                "step": "Готово",
                "message": message,
                "finished_at": timezone.now(),
                "result": result,
            },
        )
    except Exception as exc:
        log_user = None
        log_seller = None
        if isinstance(user_id, int):
            from django.contrib.auth.models import User
            log_user = User.objects.filter(id=user_id).first()
        if isinstance(seller_id, int):
            log_seller = SellerAccount.objects.filter(id=seller_id).first()
        _log_app_error(
            source="sync.worker",
            message=f"Ошибка синхронизации: {exc}",
            user=log_user,
            seller=log_seller,
            context={"task_id": task_id},
            traceback_text=traceback.format_exc(),
        )
        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "error",
                "progress": 100,
                "step": "Ошибка",
                "message": f"Ошибка синхронизации: {exc}",
                "finished_at": timezone.now(),
                "result": {},
            },
        )
    finally:
        close_old_connections()


def home(request):
    if not request.user.is_authenticated:
        return render(request, "home_landing.html")

    seller = _get_or_create_seller_for_user(request.user)

    if request.method == "POST" and request.POST.get("action") == "sync_orders":
        api_token = (seller.api_token or "").strip()
        if not api_token:
            messages.error(request, "Сначала добавьте API-ключ в настройках аккаунта.")
            return redirect("home")
        try:
            products_count = sync_products_content(seller)
            tariffs_count = sync_warehouse_tariffs(seller)
            acceptance_coeffs_count = sync_acceptance_coefficients(seller)
            offices_count = sync_wb_offices(seller)
            transit_tariffs_count = sync_transit_direction_tariffs(seller)
            orders_count = sync_fbw_orders(seller, days_back=175)
            stocks_count = sync_supplier_stocks(seller)
            realization_upserted_rows = 0
            realization_sync_error = None
            try:
                today = timezone.localdate()
                realization_result = sync_realization_report_detail(
                    seller=seller,
                    date_from=today - timedelta(days=175),
                    date_to=today,
                    period="weekly",
                    respect_rate_limit=False,
                )
                realization_upserted_rows = int(realization_result.get("upserted_rows") or 0)
            except Exception as exc:
                realization_sync_error = str(exc)
            messages.success(
                request,
                (
                    f"Синхронизация завершена: карточек {products_count}, тарифов коробов {tariffs_count}, "
                    f"тарифов приемки {acceptance_coeffs_count}, "
                    f"транзитных направлений {transit_tariffs_count}, складов {offices_count}, заказов {orders_count}, "
                    f"остатков {stocks_count}, "
                    f"строк отчета реализации {realization_upserted_rows}."
                ),
            )
            if realization_sync_error:
                messages.warning(
                    request,
                    f"Синхронизация отчетов реализации пропущена: {realization_sync_error}",
                )
        except Exception as exc:
            messages.error(request, f"Ошибка синхронизации: {exc}")
        return redirect("home")

    local_orders_percent = None
    local_orders_trend = {"points": []}
    fact_localization_index_trend = {"points": []}
    theoretical_localization_index_trend = {"points": []}
    top_non_local_districts = {"points": []}
    missing_api_token = not seller or not (seller.api_token or "").strip()

    if seller:
        local_orders_percent = get_local_orders_percent_last_full_week(seller)
        local_orders_trend = get_local_orders_percent_trend_last_full_weeks(seller, weeks=25)
        fact_localization_index_trend = get_fact_localization_index_trend_last_full_weeks(seller, weeks=25)
        theoretical_localization_index_trend = get_theoretical_localization_index_trend_last_full_weeks(seller, weeks=25)
        top_non_local_districts = get_top_non_local_districts_last_full_weeks(seller, weeks=13, limit=5)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    return render(
        request,
        "home.html",
        {
            "local_orders_percent": local_orders_percent,
            "local_orders_trend": local_orders_trend,
            "fact_localization_index_trend": fact_localization_index_trend,
            "theoretical_localization_index_trend": theoretical_localization_index_trend,
            "top_non_local_districts": top_non_local_districts,
            "seller": seller,
            "missing_api_token": missing_api_token,
            "last_sync_at": last_sync_at,
        },
    )


@login_required
@require_POST
def sync_orders_start_api(request):
    try:
        seller = _get_or_create_seller_for_user(request.user)
        api_token = (seller.api_token or "").strip()
        if not api_token:
            return JsonResponse(
                {"error": "Сначала добавьте API-ключ в настройках аккаунта."},
                status=400,
            )

        task_id = uuid.uuid4().hex
        _set_sync_task(
            task_id,
            {
                "task_id": task_id,
                "status": "running",
                "progress": 0,
                "step": "Инициализация",
                "message": "Задача синхронизации запущена...",
                "finished_at": None,
                "result": {},
                "user_id": request.user.id,
                "seller_id": seller.id,
            },
        )

        worker = threading.Thread(
            target=_run_sync_orders_task,
            args=(task_id, seller.id, request.user.id),
            daemon=True,
        )
        worker.start()
        return JsonResponse({"task_id": task_id, "status": "running"}, status=202)
    except Exception as exc:
        _log_app_error(
            source="sync.start_api",
            message=f"Не удалось запустить синхронизацию: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": f"Не удалось запустить синхронизацию: {exc}"}, status=500)


@login_required
@require_GET
def sync_orders_status_api(request):
    try:
        task_id = (request.GET.get("task_id") or "").strip()
        if not task_id:
            return JsonResponse({"error": "task_id is required"}, status=400)

        task = _get_sync_task(task_id)
        if not task:
            return JsonResponse({"error": "task not found"}, status=404)
        if task.user_id != request.user.id:
            return JsonResponse({"error": "forbidden"}, status=403)

        payload = {
            "task_id": task.task_id,
            "status": task.status,
            "progress": task.progress,
            "step": task.step,
            "message": task.message,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "result": task.result or {},
        }
        return JsonResponse(payload, status=200)
    except Exception as exc:
        _log_app_error(
            source="sync.status_api",
            message=f"Не удалось получить статус синхронизации: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            context={"task_id": request.GET.get("task_id")},
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": f"Не удалось получить статус синхронизации: {exc}"}, status=500)


@login_required
def replenishment_report(request):
    seller = _get_seller_for_user(request.user)
    data = calculate_replenishment(seller) if seller else []
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    return render(
        request,
        "replenishment/report.html",
        {"rows": data, "seller": seller, "last_sync_at": last_sync_at}
    )


@login_required
def account_settings(request):
    seller = _get_or_create_seller_for_user(request.user)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "purge_seller_data":
            confirmed = (request.POST.get("confirm_purge_seller_data") or "").strip() == "1"
            if not confirmed:
                messages.error(request, "Удаление данных отменено: не подтверждено.")
                return redirect(reverse("account_settings"))

            deleted_summary = {
                "products": Product.objects.filter(seller=seller).delete()[0],
                "orders": Order.objects.filter(seller=seller).delete()[0],
                "stocks": WarehouseStockDetailed.objects.filter(seller=seller).delete()[0],
                "tariffs": WbWarehouseTariff.objects.filter(seller=seller).delete()[0],
                "acceptance": WbAcceptanceCoefficient.objects.filter(seller=seller).delete()[0],
                "transit": TransitDirectionTariff.objects.filter(seller=seller).delete()[0],
                "realization": RealizationReportDetail.objects.filter(seller=seller).delete()[0],
                "sync_tasks": SyncTask.objects.filter(seller=seller).delete()[0],
                "feedback": TesterFeedback.objects.filter(seller=seller).delete()[0],
                "errors": AppErrorLog.objects.filter(seller=seller).delete()[0],
            }
            total_deleted = sum(deleted_summary.values())
            messages.success(
                request,
                (
                    "Данные seller очищены. "
                    f"Удалено записей: {total_deleted} "
                    f"(заказы: {deleted_summary['orders']}, товары: {deleted_summary['products']})."
                ),
            )
            return redirect(reverse("account_settings"))

        if action == "delete_account":
            confirmed = (request.POST.get("confirm_delete_account") or "").strip() == "1"
            if not confirmed:
                messages.error(request, "Удаление аккаунта отменено: не подтверждено.")
                return redirect(reverse("account_settings"))

            user = request.user
            logout(request)
            user.delete()
            return redirect(reverse("home"))

        if action == "import_orders_excel":
            excel_file = request.FILES.get("orders_excel")
            if not excel_file:
                messages.error(request, "Выберите Excel-файл для импорта.")
                return redirect(reverse("account_settings"))

            if not excel_file.name.lower().endswith(".xlsx"):
                messages.error(request, "Поддерживается только формат .xlsx")
                return redirect(reverse("account_settings"))

            try:
                result = import_orders_from_excel(seller=seller, file_obj=excel_file)
                messages.success(
                    request,
                    (
                        "Импорт завершен: "
                        f"добавлено {result.get('created', 0)}, "
                        f"обновлено {result.get('updated', 0)}, "
                        f"пропущено {result.get('skipped', 0)}."
                    ),
                )
            except Exception as exc:
                _log_app_error(
                    source="account.import_orders_excel",
                    message=f"Ошибка импорта Excel: {exc}",
                    user=request.user,
                    seller=seller,
                    path=request.path,
                    traceback_text=traceback.format_exc(),
                )
                messages.error(request, f"Ошибка импорта Excel: {exc}")

            return redirect(reverse("account_settings"))

        seller.api_token = request.POST.get("api_token", "").strip()
        seller.save(update_fields=["api_token"])
        return redirect(f"{reverse('account_settings')}?saved=1")

    return render(
        request,
        "account/settings.html",
        {
            "seller": seller,
            "saved": request.GET.get("saved") == "1",
        },
    )


@login_required
def supply_recommendations_report(request):
    seller = _get_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    default_date_to = current_month_start - timedelta(days=1)
    default_date_from = default_date_to.replace(day=1)

    # Для демо-пользователей без API-ключа (обычно с Excel-импортом за месяц)
    # дефолтный диапазон берем из фактически загруженных заказов.
    if seller and not (seller.api_token or "").strip():
        bounds = (
            Order.objects
            .filter(seller=seller)
            .aggregate(min_dt=Min("order_date"), max_dt=Max("order_date"))
        )
        min_dt = bounds.get("min_dt")
        max_dt = bounds.get("max_dt")
        if min_dt and max_dt:
            default_date_from = min_dt.date()
            default_date_to = max_dt.date()

    transit_warehouses = list_available_transit_warehouses(seller=seller)
    main_warehouses = list_regular_warehouses(seller=seller)
    default_transit_warehouse = (
        "Обухово" if "Обухово" in transit_warehouses else (transit_warehouses[0] if transit_warehouses else "")
    )
    default_main_warehouse = "Электросталь" if "Электросталь" in main_warehouses else (main_warehouses[0] if main_warehouses else "")
    return render(
        request,
        "recommendations/report.html",
        {
            "seller": seller,
            "default_date_from": default_date_from.isoformat(),
            "default_date_to": default_date_to.isoformat(),
            "transit_warehouses": transit_warehouses,
            "default_transit_warehouse": default_transit_warehouse,
            "main_warehouses": main_warehouses,
            "default_main_warehouse": default_main_warehouse,
            "last_sync_at": last_sync_at,
        },
    )


@login_required
@require_GET
def dashboard_supply_recommendations_api(request):
    date_from_raw = request.GET.get("date_from")
    date_to_raw = request.GET.get("date_to")
    transit_warehouse = (request.GET.get("transit_warehouse") or "").strip()
    main_warehouse = (request.GET.get("main_warehouse") or "").strip()
    include_food = (request.GET.get("include_food") or "").strip().lower() in {"1", "true", "yes", "on"}

    if not date_from_raw or not date_to_raw:
        return JsonResponse(
            {"error": "date_from and date_to are required query params in YYYY-MM-DD format"},
            status=400,
        )

    try:
        date_from = date.fromisoformat(date_from_raw)
        date_to = date.fromisoformat(date_to_raw)
    except ValueError:
        return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

    if date_from > date_to:
        return JsonResponse({"error": "date_from must be <= date_to"}, status=400)

    try:
        seller = _get_seller_for_user(request.user)
        transit_warehouse = _resolve_name_case_insensitive(
            transit_warehouse,
            list_available_transit_warehouses(seller=seller),
        ) or ""
        main_warehouse = _resolve_name_case_insensitive(
            main_warehouse,
            list_regular_warehouses(seller=seller),
        ) or ""

        payload = get_dashboard_supply_recommendations(
            date_from=date_from,
            date_to=date_to,
            seller=seller,
            transit_warehouse=transit_warehouse or None,
            main_warehouse=main_warehouse or None,
            include_food=include_food,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        _log_app_error(
            source="recommendations.api",
            message=f"Internal error while building recommendations: {exc}",
            user=request.user,
            seller=_get_seller_for_user(request.user),
            path=request.path,
            context={
                "date_from": date_from_raw,
                "date_to": date_to_raw,
                "transit_warehouse": transit_warehouse,
                "main_warehouse": main_warehouse,
                "include_food": include_food,
            },
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Internal error while building recommendations"}, status=500)

    return JsonResponse(payload, status=200)


@login_required
def acceptance_coefficients_report(request):
    seller = _get_or_create_seller_for_user(request.user)
    last_sync_at = _get_last_sync_at_for_user(request.user, seller)

    if request.method == "POST" and request.POST.get("action") == "sync_acceptance":
        api_token = (seller.api_token or "").strip()
        if not api_token:
            messages.error(request, "Сначала добавьте API-ключ в настройках аккаунта.")
            return redirect("acceptance_coefficients_report")
        try:
            synced = sync_acceptance_coefficients(seller)
            messages.success(request, f"Синхронизация коэффициентов приёмки завершена: {synced} строк.")
        except Exception as exc:
            _log_app_error(
                source="acceptance.sync",
                message=f"Ошибка синхронизации коэффициентов приёмки: {exc}",
                user=request.user,
                seller=seller,
                path=request.path,
                traceback_text=traceback.format_exc(),
            )
            messages.error(request, f"Ошибка синхронизации коэффициентов приёмки: {exc}")
        return redirect("acceptance_coefficients_report")

    date_from_raw = request.GET.get("date_from")
    date_to_raw = request.GET.get("date_to")
    warehouse_query = (request.GET.get("warehouse") or "").strip()
    box_type = (request.GET.get("box_type") or "2").strip()
    only_available = request.GET.get("only_available", "1") == "1"
    hide_sc = request.GET.get("hide_sc") == "1"
    hide_food = request.GET.get("hide_food", "1") == "1"

    today = timezone.localdate()
    default_date_from = today
    default_date_to = today + timedelta(days=13)

    try:
        date_from = date.fromisoformat(date_from_raw) if date_from_raw else default_date_from
    except ValueError:
        date_from = default_date_from
    try:
        date_to = date.fromisoformat(date_to_raw) if date_to_raw else default_date_to
    except ValueError:
        date_to = default_date_to

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    qs = WbAcceptanceCoefficient.objects.filter(
        seller=seller,
        coeff_date__gte=date_from,
        coeff_date__lte=date_to,
    )
    if box_type != "all":
        try:
            qs = qs.filter(box_type_id=int(box_type))
        except ValueError:
            box_type = "2"
            qs = qs.filter(box_type_id=2)
    normalized_warehouse_query = _normalize_name_for_match(warehouse_query)
    if only_available:
        # "Доступные к отгрузке" = разрешена отгрузка и нет запрета по коэффициенту.
        qs = qs.filter(allow_unload=True).exclude(coefficient__lt=0)

    raw_rows = list(qs.order_by("warehouse_name", "coeff_date"))

    def _is_sc_name(name: str) -> bool:
        normalized = _normalize_name_for_match(name)
        return normalized.startswith("сц ")

    def _is_food_name(name: str) -> bool:
        normalized = _normalize_name_for_match(name)
        return "питание" in normalized

    filtered_rows = []
    for row in raw_rows:
        warehouse_name = (row.warehouse_name or "").strip()
        if not warehouse_name:
            continue
        if hide_sc and (_is_sc_name(warehouse_name) or bool(row.is_sorting_center)):
            continue
        if hide_food and _is_food_name(warehouse_name):
            continue
        if normalized_warehouse_query and normalized_warehouse_query not in _normalize_name_for_match(warehouse_name):
            continue
        filtered_rows.append(row)

    warehouse_options_qs = (
        WbAcceptanceCoefficient.objects
        .filter(seller=seller)
        .exclude(warehouse_name__isnull=True)
        .exclude(warehouse_name__exact="")
    )
    if box_type != "all":
        try:
            warehouse_options_qs = warehouse_options_qs.filter(box_type_id=int(box_type))
        except ValueError:
            pass
    warehouse_options = sorted(
        set(warehouse_options_qs.values_list("warehouse_name", flat=True))
    )

    date_columns = []
    current = date_from
    while current <= date_to:
        date_columns.append(current)
        current += timedelta(days=1)

    def _fmt_value(value):
        if value is None:
            return "-"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _fmt_pair(first, second):
        if first is None and second is None:
            return "-"
        return f"{_fmt_value(first)} / {_fmt_value(second)}"

    def _fmt_coef(value):
        if value is None:
            return ""
        return f"{value:.0f}%"

    if box_type == "5":
        delivery_title = "Логистика, ₽ за паллету"
        storage_title = "Хранение, ₽ за паллету"
    else:
        delivery_title = "Логистика, ₽ (1-й / доп. литр)"
        storage_title = "Хранение, ₽ (1-й / доп. литр)"

    def _format_delivery(row):
        if box_type == "5":
            return _fmt_value(row.delivery_base_liter)
        return _fmt_pair(row.delivery_base_liter, row.delivery_additional_liter)

    def _format_storage(row):
        if box_type == "5":
            return _fmt_value(row.storage_base_liter)
        return _fmt_pair(row.storage_base_liter, row.storage_additional_liter)

    matrix = {}
    warehouse_names = set()
    for row in filtered_rows:
        warehouse_name = (row.warehouse_name or "").strip()
        if not warehouse_name:
            continue
        warehouse_names.add(warehouse_name)

        if not row.allow_unload or (row.coefficient is not None and row.coefficient < 0):
            acceptance_label = "Недоступно"
            acceptance_class = "pill-bad"
        elif row.coefficient == 0:
            acceptance_label = "Бесплатно"
            acceptance_class = "pill-ok"
        elif row.coefficient is None:
            acceptance_label = "-"
            acceptance_class = "pill-neutral"
        else:
            acceptance_label = f"x{_fmt_value(row.coefficient)}"
            acceptance_class = "pill-warn"

        matrix[(warehouse_name, row.coeff_date)] = {
            "acceptance_label": acceptance_label,
            "acceptance_class": acceptance_class,
            "logistics_text": _format_delivery(row),
            "logistics_coef_text": _fmt_coef(row.delivery_coef),
            "storage_text": _format_storage(row),
            "storage_coef_text": _fmt_coef(row.storage_coef),
        }

    warehouse_rows = []
    for warehouse_name in sorted(warehouse_names):
        cells = [matrix.get((warehouse_name, dt)) for dt in date_columns]
        warehouse_rows.append({"warehouse_name": warehouse_name, "cells": cells})

    return render(
        request,
        "tariffs/acceptance_coefficients.html",
        {
            "seller": seller,
            "last_sync_at": last_sync_at,
            "date_columns": date_columns,
            "warehouse_rows": warehouse_rows,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "warehouse_query": warehouse_query,
            "warehouse_options": warehouse_options,
            "box_type": box_type,
            "delivery_title": delivery_title,
            "storage_title": storage_title,
            "only_available": only_available,
            "hide_sc": hide_sc,
            "hide_food": hide_food,
        },
    )


@login_required
@require_POST
def create_feedback_api(request):
    message = (request.POST.get("message") or "").strip()
    category = (request.POST.get("category") or TesterFeedback.CATEGORY_BUG).strip()
    priority = (request.POST.get("priority") or TesterFeedback.PRIORITY_MEDIUM).strip()
    page_url = (request.POST.get("page_url") or "").strip()
    include_context = (request.POST.get("include_context") or "1").strip() in {"1", "true", "True", "on"}
    raw_context = (request.POST.get("context_json") or "").strip()

    if not message or len(message) < 5:
        return JsonResponse({"error": "Комментарий слишком короткий (минимум 5 символов)."}, status=400)

    valid_categories = {value for value, _ in TesterFeedback.CATEGORY_CHOICES}
    valid_priorities = {value for value, _ in TesterFeedback.PRIORITY_CHOICES}
    if category not in valid_categories:
        category = TesterFeedback.CATEGORY_BUG
    if priority not in valid_priorities:
        priority = TesterFeedback.PRIORITY_MEDIUM

    context_json = {}
    if include_context:
        context_json = {
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "referer": request.META.get("HTTP_REFERER", ""),
            "path": request.path,
            "posted_at": timezone.now().isoformat(),
        }
        if raw_context:
            try:
                parsed = json.loads(raw_context)
                if isinstance(parsed, dict):
                    context_json.update(parsed)
            except json.JSONDecodeError:
                context_json["raw_context"] = raw_context[:1000]

    seller = _get_seller_for_user(request.user)

    try:
        item = TesterFeedback.objects.create(
            user=request.user,
            seller=seller,
            page_url=page_url,
            category=category,
            priority=priority,
            message=message,
            include_context=include_context,
            context_json=context_json,
        )
        return JsonResponse({"ok": True, "ticket_id": item.id}, status=201)
    except Exception as exc:
        _log_app_error(
            source="feedback.create_api",
            message=f"Не удалось сохранить фидбек: {exc}",
            user=request.user,
            seller=seller,
            path=request.path,
            traceback_text=traceback.format_exc(),
        )
        return JsonResponse({"error": "Не удалось сохранить сообщение. Попробуйте ещё раз."}, status=500)
