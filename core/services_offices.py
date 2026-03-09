from .models import WbOffice
from wb_api.client import WBMarketplaceClient
from core.services.localization import clear_offices_cache


def sync_wb_offices(seller):
    client = WBMarketplaceClient(seller.api_token)

    offices = client.get_offices()

    for o in offices:
        WbOffice.objects.update_or_create(
            office_id=o["id"],
            defaults={
                "name": o["name"],
                "city": o["city"],
                "address": o["address"],
                "federal_district": o.get("federalDistrict"),
                "longitude": o.get("longitude"),
                "latitude": o.get("latitude"),
            }
        )

    # После обновления справочника складов сбрасываем кеш матчинга.
    clear_offices_cache()

    print(f"Складов синхронизировано: {len(offices)}")
