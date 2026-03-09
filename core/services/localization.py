from datetime import timedelta
import re
from types import SimpleNamespace
from django.db.models import Count, Q
from django.utils import timezone
from core.models import Order, WbOffice


_OFFICES_CACHE = None

# Явные фиксы для складов, которые приходят в заказах, но могут отсутствовать
# или называться иначе в справочнике WB офисов.
MANUAL_WAREHOUSE_DISTRICT_OVERRIDES = {
    "электросталь": "Центральный федеральный округ",
    "тула": "Центральный федеральный округ",
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

    if "Юж" in name or "Кавказ" in name:
        return "Юг"

    if "Сибир" in name or "Дальневост" in name:
        return "Восток"

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
