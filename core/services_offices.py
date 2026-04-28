from .models import WbOffice
from wb_api.client import WBMarketplaceClient
from core.services.localization import clear_offices_cache


def sync_wb_offices(seller):
    client = WBMarketplaceClient(seller.api_token_plain)

    offices = client.get_offices()

    prepared_rows: list[tuple[int, dict]] = []
    for o in offices:
        office_id = o.get("id")
        if office_id is None:
            continue
        prepared_rows.append(
            (
                int(office_id),
                {
                    "name": o.get("name"),
                    "city": o.get("city"),
                    "address": o.get("address"),
                    "federal_district": o.get("federalDistrict"),
                    "longitude": o.get("longitude"),
                    "latitude": o.get("latitude"),
                },
            )
        )

    if prepared_rows:
        office_ids = [row[0] for row in prepared_rows]
        existing_map = {int(item.office_id): item for item in WbOffice.objects.filter(office_id__in=office_ids)}
        to_create: list[WbOffice] = []
        to_update: list[WbOffice] = []
        for office_id, defaults in prepared_rows:
            existing = existing_map.get(office_id)
            if existing is None:
                to_create.append(WbOffice(office_id=office_id, **defaults))
                continue
            existing.name = defaults["name"]
            existing.city = defaults["city"]
            existing.address = defaults["address"]
            existing.federal_district = defaults["federal_district"]
            existing.longitude = defaults["longitude"]
            existing.latitude = defaults["latitude"]
            to_update.append(existing)

        if to_create:
            WbOffice.objects.bulk_create(to_create, batch_size=2000)
        if to_update:
            WbOffice.objects.bulk_update(
                to_update,
                ["name", "city", "address", "federal_district", "longitude", "latitude"],
                batch_size=2000,
            )

    # После обновления справочника складов сбрасываем кеш матчинга.
    clear_offices_cache()

    return len(offices)
