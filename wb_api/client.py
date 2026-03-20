import requests
from typing import List, Dict
import json
from datetime import date as dt_date

class WBStocksSupplierClient:
    """
    Клиент для метода /supplier/stocks
    Возвращает остатки по каждому артикулу, размеру и складу.
    """

    BASE_URL = "https://statistics-api.wildberries.ru/api/v1/supplier/stocks"

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"{self.api_token}",  # именно так требует WB Stats API
            "Accept": "application/json"
        }

    def _handle_response(self, response: requests.Response) -> Dict:
        """
        Проверка ответа API.
        Возвращает распарсенный JSON или возбуждает исключение.
        """
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"WB Stocks API Error {response.status_code}:\n{response.text}"
            )

    def get_supplier_stocks(self,) -> Dict:
        """
        Получает остатки товара.
        - limit — сколько записей брать за запрос (максимум 1000)
        - offset — смещение для пагинации
        """

        params = {
            "dateFrom": "2019-06-20T00:00:00Z",  # можно указать дату, но для синхронизации всех остатков лучше брать с большой давности
        }

        response = requests.get(
            self.BASE_URL,
            headers=self.headers,
            params=params,
            timeout=30
        )

        return self._handle_response(response)
    
class WBOrdersSupplierClient:

    BASE_URL = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json"
        }

    def _handle_response(self, response: requests.Response):
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"WB Orders API Error {response.status_code}:\n{response.text}"
            )

    def get_orders(self, date_from: str, flag: int = 0):
        params = {
            "dateFrom": date_from,
            "flag": flag
        }

        response = requests.get(
            self.BASE_URL,
            headers=self.headers,
            params=params,
            timeout=60
        )

        return self._handle_response(response)    

class WBMarketplaceClient:

    BASE_URL = "https://marketplace-api.wildberries.ru/api/v3"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json",
        }

    def get_offices(self):
        """
        Получить список складов WB
        """
        url = f"{self.BASE_URL}/offices"

        response = requests.get(
            url,
            headers=self.headers,
            timeout=30
        )

        if response.status_code != 200:
            raise Exception(
                f"WB Offices API Error {response.status_code}: {response.text}"
            )

        return response.json()


class WBContentClient:
    """
    Клиент для WB Content API карточек товаров.
    """

    BASE_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "accept": "application/json",
        }

    @staticmethod
    def _to_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _calc_volume_liters(cls, dimensions: Dict) -> float:
        if not isinstance(dimensions, dict):
            return 0.0
        length = cls._to_float(dimensions.get("length"))
        width = cls._to_float(dimensions.get("width"))
        height = cls._to_float(dimensions.get("height"))
        volume = (length * width * height) / 1000.0
        return round(volume, 3)

    @classmethod
    def _extract_card_payload(cls, card: Dict) -> Dict:
        dimensions = card.get("dimensions", {}) or {}
        return {
            "nm_id": card.get("nmID"),
            "vendor_code": card.get("vendorCode"),
            "brand": card.get("brand"),
            "weight_kg": cls._to_float(dimensions.get("weightBrutto"), default=0.0),
            "volume_liters": cls._calc_volume_liters(dimensions),
        }

    def get_cards_list(self, limit: int = 100) -> List[Dict]:
        payload = {
            "settings": {
                "cursor": {"limit": limit},
                "filter": {"withPhoto": -1},
            }
        }

        all_cards: List[Dict] = []
        total = limit

        while total == limit:
            response = requests.post(
                self.BASE_URL,
                headers=self.headers,
                data=json.dumps(payload),
                timeout=60,
            )
            if response.status_code != 200:
                raise Exception(f"WB Content API Error {response.status_code}:\n{response.text}")

            data = response.json()
            cards = data.get("cards", []) or []
            all_cards.extend(cards)

            cursor = data.get("cursor", {}) or {}
            total = cursor.get("total", 0)

            if total == limit:
                payload["settings"]["cursor"] = {
                    "updatedAt": cursor.get("updatedAt"),
                    "nmID": cursor.get("nmID"),
                    "limit": limit,
                }

        return all_cards


class WBCommonClient:
    """
    Клиент для Common API WB тарифов коробов.
    """

    BASE_URL = "https://common-api.wildberries.ru/api/v1/tariffs/box"
    ACCEPTANCE_COEFFICIENTS_URL = "https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "accept": "application/json",
        }

    def get_tariffs_box(self, on_date: dt_date | None = None) -> List[Dict]:
        query_date = (on_date or dt_date.today()).isoformat()
        response = requests.get(
            self.BASE_URL,
            headers=self.headers,
            params={"date": query_date},
            timeout=30,
        )
        if response.status_code != 200:
            raise Exception(f"WB Common API Error {response.status_code}:\n{response.text}")

        payload = response.json()
        return (
            payload.get("response", {})
            .get("data", {})
            .get("warehouseList", [])
        )

    def get_acceptance_coefficients(self, warehouse_ids: list[int] | None = None) -> List[Dict]:
        params = {}
        if warehouse_ids:
            params["warehouseIDs"] = ",".join(str(warehouse_id) for warehouse_id in warehouse_ids)

        response = requests.get(
            self.ACCEPTANCE_COEFFICIENTS_URL,
            headers=self.headers,
            params=params,
            timeout=30,
        )
        if response.status_code != 200:
            raise Exception(f"WB Common API Error {response.status_code}:\n{response.text}")

        payload = response.json()
        if isinstance(payload, list):
            return payload
        return []


class WBSuppliesClient:
    """
    Клиент для WB Supplies API.
    """

    BASE_URL = "https://supplies-api.wildberries.ru/api/v1/transit-tariffs"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "accept": "application/json",
        }

    def get_transit_tariffs(self) -> List[Dict]:
        response = requests.get(
            self.BASE_URL,
            headers=self.headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise Exception(f"WB Supplies API Error {response.status_code}:\n{response.text}")
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return []


class WBStatisticsReportsClient:
    """
    Клиент для Statistics API: детализация отчётов реализации.
    """

    BASE_URL = "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json",
        }

    def get_report_detail_by_period(
        self,
        date_from: str,
        date_to: str,
        limit: int = 100000,
        rrdid: int = 0,
        period: str = "weekly",
    ) -> tuple[int, List[Dict]]:
        response = requests.get(
            self.BASE_URL,
            headers=self.headers,
            params={
                "dateFrom": date_from,
                "dateTo": date_to,
                "limit": limit,
                "rrdid": rrdid,
                "period": period,
            },
            timeout=90,
        )
        if response.status_code == 204:
            return (204, [])
        if response.status_code != 200:
            raise Exception(f"WB ReportDetail API Error {response.status_code}:\n{response.text}")

        payload = response.json()
        if isinstance(payload, list):
            return (200, payload)
        return (200, [])
