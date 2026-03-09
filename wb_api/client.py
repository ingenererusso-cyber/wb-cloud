import requests
from typing import List, Dict

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