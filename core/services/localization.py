from datetime import date, timedelta
import re
from types import SimpleNamespace
from django.db.models import Count, Q
from django.utils import timezone
from core.logistics import get_ktr_for_share, LOCALIZATION_COEFFICIENTS_TABLE
from core.models import Order, WbOffice


_OFFICES_CACHE = None

# Явные фиксы для складов, которые приходят в заказах, но могут отсутствовать
# или называться иначе в справочнике WB офисов.
MANUAL_WAREHOUSE_DISTRICT_OVERRIDES = {
    "электросталь": "Центральный федеральный округ",
    "тула": "Центральный федеральный округ",
    "владимир": "Центральный федеральный округ",
}


def get_offices_cache():
    global _OFFICES_CACHE

    if _OFFICES_CACHE is None:
        _OFFICES_CACHE = list(WbOffice.objects.all())

    return _OFFICES_CACHE


def clear_offices_cache():
    global _OFFICES_CACHE
    _OFFICES_CACHE = None


def normalize_district(name: str | None) -> str | None:
    if not name:
        return None

    name = name.strip()
    lowered = name.lower().replace("ё", "е")

    if "юж" in lowered or "кавказ" in lowered:
        return "Юг"

    if "сибир" in lowered or "дальневост" in lowered:
        return "Восток"

    # Excel-выгрузки и некоторые отчеты дают укороченные названия округов.
    if "централь" in lowered:
        return "Центральный федеральный округ"
    if "северо-запад" in lowered:
        return "Северо-Западный федеральный округ"
    if "приволж" in lowered:
        return "Приволжский федеральный округ"
    if "ураль" in lowered:
        return "Уральский федеральный округ"

    return name


def extract_first_word(warehouse_name: str) -> str:
    return warehouse_name.strip().split()[0]


def normalize_warehouse_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.lower().replace("ё", "е")
    # Оставляем буквы/цифры/пробелы, убираем служебные символы.
    value = re.sub(r"[^0-9a-zа-я\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def get_manual_office_override(warehouse_name: str):
    normalized_name = normalize_warehouse_text(warehouse_name)
    if not normalized_name:
        return None

    for alias, district in MANUAL_WAREHOUSE_DISTRICT_OVERRIDES.items():
        if alias in normalized_name:
            return SimpleNamespace(
                name=f"manual:{alias}",
                city=None,
                address=None,
                federal_district=district,
            )

    return None


def find_office(order_warehouse_name: str):
    if not order_warehouse_name:
        return None

    manual_office = get_manual_office_override(order_warehouse_name)
    if manual_office:
        return manual_office

    warehouse_name = normalize_warehouse_text(order_warehouse_name)
    if not warehouse_name:
        return None

    first_word = extract_first_word(order_warehouse_name).lower()
    tokens = [t for t in warehouse_name.split() if len(t) >= 4]
    token_set = set(tokens)

    for office in get_offices_cache():
        office_name = normalize_warehouse_text(office.name)
        office_city = normalize_warehouse_text(office.city)
        office_address = normalize_warehouse_text(office.address)
        office_blob = " ".join([office_name, office_city, office_address]).strip()

        # Пробуем полное совпадение/вхождение названия.
        if warehouse_name in office_blob or office_name in warehouse_name:
            return office

        # Затем матч по значимым токенам (например, "волгоград").
        office_tokens = {t for t in office_blob.split() if len(t) >= 4}
        if token_set and token_set.intersection(office_tokens):
            return office

        # Фолбэк на старую логику по первому слову.
        if first_word in office_blob:
            return office

    return None

def determine_locality(warehouse_name: str, oblast_okrug_name: str) -> bool:
    office = find_office(warehouse_name)

    if not office:
        return False

    order_district = normalize_district(oblast_okrug_name)
    office_district = normalize_district(office.federal_district)

    if not order_district or not office_district:
        return False

    return order_district == office_district


def _get_local_orders_stats_for_period(seller, start_date, end_date):
    counters = (
        Order.objects
        .filter(
            seller=seller,
            order_date__date__gte=start_date,
            order_date__date__lte=end_date,
            warehouse_type="Склад WB",
            is_cancel=False,
        )
        .aggregate(
            total=Count("id"),
            local=Count("id", filter=Q(is_local=True)),
        )
    )

    total_orders = counters["total"] or 0
    local_orders = counters["local"] or 0
    percent = round((local_orders / total_orders) * 100, 1) if total_orders else 0.0

    return {
        "percent": percent,
        "local_orders": local_orders,
        "total_orders": total_orders,
        "date_from": start_date,
        "date_to": end_date,
    }


def get_local_orders_percent_last_full_week(seller):
    yesterday = timezone.localdate() - timedelta(days=1)
    current_start = yesterday - timedelta(days=6)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)

    current = _get_local_orders_stats_for_period(seller, current_start, yesterday)
    previous = _get_local_orders_stats_for_period(seller, previous_start, previous_end)

    prev_percent = previous["percent"]
    curr_percent = current["percent"]

    change_percent = round(curr_percent - prev_percent, 1)

    if change_percent > 0:
        change_direction = "up"
    elif change_percent < 0:
        change_direction = "down"
    else:
        change_direction = "neutral"

    return {
        **current,
        "previous_percent": prev_percent,
        "change_percent": change_percent,
        "change_direction": change_direction,
        "previous_date_from": previous_start,
        "previous_date_to": previous_end,
    }


def get_local_orders_percent_trend_last_full_weeks(seller, weeks=25):
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    last_full_week_end = current_week_start - timedelta(days=1)

    points = []
    for offset in range(weeks - 1, -1, -1):
        week_end = last_full_week_end - timedelta(days=offset * 7)
        week_start = week_end - timedelta(days=6)

        counters = (
            Order.objects
            .filter(
                seller=seller,
                order_date__date__gte=week_start,
                order_date__date__lte=week_end,
                warehouse_type="Склад WB",
                is_cancel=False,
            )
            .aggregate(
                total=Count("id"),
                local=Count("id", filter=Q(is_local=True)),
            )
        )

        total_orders = counters["total"] or 0
        local_orders = counters["local"] or 0
        percent = round((local_orders / total_orders) * 100, 1) if total_orders else 0.0

        points.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "label": f"{week_start.strftime('%d.%m')}-{week_end.strftime('%d.%m')}",
                "percent": percent,
                "local_orders": local_orders,
                "total_orders": total_orders,
            }
        )

    return {
        "start_date": points[0]["week_start"] if points else None,
        "end_date": points[-1]["week_end"] if points else None,
        "start_label": points[0]["label"].split("-")[0] if points else None,
        "end_label": points[-1]["label"].split("-")[1] if points else None,
        "points": points,
    }


def get_top_non_local_districts_last_full_weeks(seller, weeks=25, limit=5):
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    end_date = current_week_start - timedelta(days=1)
    start_date = end_date - timedelta(days=weeks * 7 - 1)

    rows = (
        Order.objects
        .filter(
            seller=seller,
            order_date__date__gte=start_date,
            order_date__date__lte=end_date,
            warehouse_type="Склад WB",
            is_cancel=False,
        )
        .values("oblast_okrug_name")
        .annotate(
            total_orders=Count("id"),
            non_local_orders=Count("id", filter=Q(is_local=False)),
        )
        .order_by("oblast_okrug_name")
    )

    grouped = {}
    for row in rows:
        district_name = normalize_district(row["oblast_okrug_name"]) or "Не указан"
        total_orders = row["total_orders"] or 0
        non_local_orders = row["non_local_orders"] or 0
        if district_name not in grouped:
            grouped[district_name] = {"total_orders": 0, "non_local_orders": 0}
        grouped[district_name]["total_orders"] += total_orders
        grouped[district_name]["non_local_orders"] += non_local_orders

    points = []
    for district_name, counters in grouped.items():
        if counters["non_local_orders"] <= 0:
            continue
        points.append(
            {
                "district_name": district_name,
                "non_local_orders": counters["non_local_orders"],
                "total_orders": counters["total_orders"],
                "non_local_share": round(
                    (counters["non_local_orders"] / counters["total_orders"]) * 100, 1
                ) if counters["total_orders"] else 0.0,
            }
        )

    points.sort(key=lambda item: (-item["non_local_orders"], item["district_name"]))
    points = points[:limit]

    return {
        "date_from": start_date,
        "date_to": end_date,
        "points": points,
    }


def calculate_theoretical_localization_index_for_period(
    seller,
    start_date,
    end_date,
    min_orders=1000,
):
    """
    Теоретический ИЛ по правилам WB (приближенно):
    - только FBW (Склад WB) и только Россия;
    - окно расчета: заданный период (обычно 13 полных недель);
    - по каждому артикулу: доля локализации -> КТР;
    - итоговый ИЛ: средний КТР, взвешенный количеством заказов.

    Ограничение текущей версии:
    - признаки заказов-исключений по спецкатегориям (КГТ+/СГТ/КБТ и т.п.)
      в данных отсутствуют, поэтому порог 35% исключений не применяется.
    """
    article_rows = (
        Order.objects
        .filter(
            seller=seller,
            is_cancel=False,
            warehouse_type="Склад WB",
            country_name="Россия",
            order_date__date__gte=start_date,
            order_date__date__lte=end_date,
        )
        .values("supplier_article")
        .annotate(
            orders_total=Count("id"),
            orders_local=Count("id", filter=Q(is_local=True)),
        )
    )

    rows = list(article_rows)
    total_orders = sum(int(r["orders_total"] or 0) for r in rows)
    if total_orders < min_orders:
        return None

    weighted_sum = 0.0
    for row in rows:
        orders_total = int(row["orders_total"] or 0)
        if orders_total <= 0:
            continue
        orders_local = int(row["orders_local"] or 0)
        local_share = (orders_local / orders_total) * 100.0
        ktr = get_ktr_for_share(local_share, as_of_date=end_date)
        weighted_sum += orders_total * ktr

    theoretical_index = (weighted_sum / total_orders) if total_orders else None
    if theoretical_index is None:
        return None

    return {
        "theoretical_index": round(theoretical_index, 6),
        "orders_total": total_orders,
    }


def _count_theoretical_orders_for_period(
    seller,
    start_date,
    end_date,
):
    return (
        Order.objects
        .filter(
            seller=seller,
            is_cancel=False,
            warehouse_type="Склад WB",
            country_name="Россия",
            order_date__date__gte=start_date,
            order_date__date__lte=end_date,
        )
        .count()
    )


def get_theoretical_localization_index_trend_last_full_weeks(seller, weeks=25, lookback_weeks=13):
    """
    Тренд теоретического ИЛ:
    - точка за каждую полную неделю;
    - для точки берем окно lookback_weeks назад (включая саму неделю);
    - если в окне < 1000 заказов, точку пропускаем.
    """
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    last_full_week_end = current_week_start - timedelta(days=1)
    min_orders = 1000

    points = []
    for offset in range(weeks - 1, -1, -1):
        week_end = last_full_week_end - timedelta(days=offset * 7)
        week_start = week_end - timedelta(days=6)
        window_start = week_end - timedelta(days=lookback_weeks * 7 - 1)

        result = calculate_theoretical_localization_index_for_period(
            seller=seller,
            start_date=window_start,
            end_date=week_end,
            min_orders=min_orders,
        )
        if result is None:
            continue

        points.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "label": f"{week_start.strftime('%d.%m')}-{week_end.strftime('%d.%m')}",
                "theoretical_index": float(result["theoretical_index"]),
                "orders_total_13w": int(result["orders_total"]),
            }
        )

    latest_window_start = last_full_week_end - timedelta(days=lookback_weeks * 7 - 1)
    latest_window_orders = _count_theoretical_orders_for_period(
        seller=seller,
        start_date=latest_window_start,
        end_date=last_full_week_end,
    )

    return {
        "start_date": points[0]["week_start"] if points else None,
        "end_date": points[-1]["week_end"] if points else None,
        "start_label": points[0]["label"].split("-")[0] if points else None,
        "end_label": points[-1]["label"].split("-")[1] if points else None,
        "points": points,
        "min_orders_required": min_orders,
        "latest_orders_13w": int(latest_window_orders),
        "no_data_reason": (
            f"Недостаточно данных: за последние {lookback_weeks} недель {int(latest_window_orders)} заказов "
            f"(нужно минимум {min_orders})."
            if not points and latest_window_orders < min_orders
            else None
        ),
    }


def _get_krp_for_share_modeled(local_share_percent: float) -> float:
    """
    KRP из таблицы без учета даты switch.
    Используется для теоретического графика ИРП, где нужно моделирование с 02.03.2026.
    """
    share = max(0.0, min(100.0, float(local_share_percent)))
    for min_share, max_share, _ktr_before, _ktr_after, krp_after in LOCALIZATION_COEFFICIENTS_TABLE:
        if min_share <= share <= max_share:
            return float(krp_after)
    return 0.0


def calculate_theoretical_irp_percent_for_period(
    seller,
    start_date,
    end_date,
):
    """
    Теоретический ИРП (в %) по окну заказов:
    ИРП = sum(orders_article * KRP_article) / sum(orders_article)
    """
    article_rows = (
        Order.objects
        .filter(
            seller=seller,
            is_cancel=False,
            warehouse_type="Склад WB",
            country_name="Россия",
            order_date__date__gte=start_date,
            order_date__date__lte=end_date,
        )
        .values("supplier_article")
        .annotate(
            orders_total=Count("id"),
            orders_local=Count("id", filter=Q(is_local=True)),
        )
    )

    rows = list(article_rows)
    total_orders = sum(int(r["orders_total"] or 0) for r in rows)
    if total_orders <= 0:
        return {
            "theoretical_irp_percent": 0.0,
            "orders_total": 0,
        }

    weighted_sum = 0.0
    for row in rows:
        orders_total = int(row["orders_total"] or 0)
        if orders_total <= 0:
            continue
        orders_local = int(row["orders_local"] or 0)
        local_share = (orders_local / orders_total) * 100.0
        krp = _get_krp_for_share_modeled(local_share)
        weighted_sum += orders_total * krp

    irp_index = (weighted_sum / total_orders) if total_orders else 0.0
    return {
        "theoretical_irp_percent": round(irp_index * 100.0, 4),
        "orders_total": total_orders,
    }


def get_theoretical_irp_trend_last_full_weeks(seller, weeks=25, lookback_weeks=13):
    """
    Тренд теоретического ИРП:
    - 25 полных недель;
    - расчет по окну последних 13 недель к каждой точке;
    - до 02.03.2026 включительно предыдущие недели = 0.
    """
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    last_full_week_end = current_week_start - timedelta(days=1)
    irp_start_date = date(2026, 3, 2)

    points = []
    for offset in range(weeks - 1, -1, -1):
        week_end = last_full_week_end - timedelta(days=offset * 7)
        week_start = week_end - timedelta(days=6)

        if week_end < irp_start_date:
            points.append(
                {
                    "week_start": week_start.isoformat(),
                    "week_end": week_end.isoformat(),
                    "label": f"{week_start.strftime('%d.%m')}-{week_end.strftime('%d.%m')}",
                    "theoretical_irp_percent": 0.0,
                    "orders_total_13w": 0,
                }
            )
            continue

        window_start = week_end - timedelta(days=lookback_weeks * 7 - 1)
        result = calculate_theoretical_irp_percent_for_period(
            seller=seller,
            start_date=window_start,
            end_date=week_end,
        )
        points.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "label": f"{week_start.strftime('%d.%m')}-{week_end.strftime('%d.%m')}",
                "theoretical_irp_percent": float(result["theoretical_irp_percent"]),
                "orders_total_13w": int(result["orders_total"]),
            }
        )

    return {
        "start_date": points[0]["week_start"] if points else None,
        "end_date": points[-1]["week_end"] if points else None,
        "start_label": points[0]["label"].split("-")[0] if points else None,
        "end_label": points[-1]["label"].split("-")[1] if points else None,
        "points": points,
    }
