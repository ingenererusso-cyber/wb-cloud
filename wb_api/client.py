import requests
from typing import List, Dict
import json
from datetime import date as dt_date
import time


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _request_with_retry(
    method: str,
    url: str,
    *,
    attempts: int = 4,
    backoff_seconds: float = 1.0,
    timeout: int = 30,
    **kwargs,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                sleep_seconds = backoff_seconds * attempt
                if response.status_code == 429:
                    retry_after_raw = (
                        response.headers.get("X-Ratelimit-Retry")
                        or response.headers.get("X-Ratelimit-Reset")
                        or response.headers.get("Retry-After")
                    )
                    try:
                        retry_after = float(retry_after_raw)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    if retry_after > 0:
                        # Добавляем небольшой запас, чтобы гарантированно выйти из окна лимита.
                        sleep_seconds = max(sleep_seconds, retry_after + 0.5)
                time.sleep(sleep_seconds)
                continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(backoff_seconds * attempt)
    raise Exception(f"WB request failed after {attempts} attempts: {method} {url}. Last error: {last_error}")

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

        response = _request_with_retry(
            "GET",
            self.BASE_URL,
            headers=self.headers,
            params=params,
            timeout=30,
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

        response = _request_with_retry(
            "GET",
            self.BASE_URL,
            headers=self.headers,
            params=params,
            timeout=60,
        )

        return self._handle_response(response)    


class WBSalesSupplierClient:
    BASE_URL = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json",
        }

    def _handle_response(self, response: requests.Response):
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, list):
                return payload
            return []
        raise Exception(
            f"WB Sales API Error {response.status_code}:\n{response.text}"
        )

    def get_sales(self, date_from: str, flag: int = 0):
        params = {
            "dateFrom": date_from,
            "flag": int(flag),
        }
        response = _request_with_retry(
            "GET",
            self.BASE_URL,
            headers=self.headers,
            params=params,
            timeout=60,
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

        response = _request_with_retry(
            "GET",
            url,
            headers=self.headers,
            timeout=30,
        )

        if response.status_code != 200:
            raise Exception(
                f"WB Offices API Error {response.status_code}: {response.text}"
            )

        return response.json()

    def get_seller_warehouses(self) -> List[Dict]:
        """
        Получить список складов продавца.
        """
        url = f"{self.BASE_URL}/warehouses"
        response = _request_with_retry(
            "GET",
            url,
            headers=self.headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise Exception(
                f"WB Seller Warehouses API Error {response.status_code}: {response.text}"
            )
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return []

    def get_seller_warehouse_stocks(self, warehouse_id: int, chrt_ids: List[int]) -> Dict:
        """
        Получить остатки на складе продавца по chrtIds.
        """
        url = f"{self.BASE_URL}/stocks/{warehouse_id}"
        headers = {
            **self.headers,
            "Content-Type": "application/json",
        }
        response = _request_with_retry(
            "POST",
            url,
            headers=headers,
            json={"chrtIds": [int(x) for x in chrt_ids if x is not None]},
            timeout=60,
        )
        if response.status_code != 200:
            raise Exception(
                f"WB Seller Stocks API Error {response.status_code}: {response.text}"
            )
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"stocks": []}

    def update_seller_warehouse_stocks(self, warehouse_id: int, stocks: List[Dict]) -> None:
        """
        Обновить остатки на складе продавца.
        Ожидаемый успешный код WB: 204.
        """
        url = f"{self.BASE_URL}/stocks/{warehouse_id}"
        headers = {
            **self.headers,
            "Content-Type": "application/json",
        }
        normalized_stocks = []
        for item in stocks:
            chrt_id = item.get("chrtId")
            amount = item.get("amount")
            if chrt_id is None or amount is None:
                continue
            normalized_stocks.append(
                {
                    "chrtId": int(chrt_id),
                    "amount": int(amount),
                }
            )

        response = _request_with_retry(
            "PUT",
            url,
            headers=headers,
            json={"stocks": normalized_stocks},
            timeout=60,
        )
        if response.status_code != 204:
            raise Exception(
                f"WB Update Seller Stocks API Error {response.status_code}: {response.text}"
            )


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
    def _extract_first_photo_url(cls, card: Dict) -> str | None:
        photos = card.get("photos") or []
        if not isinstance(photos, list) or not photos:
            return None
        first = photos[0] or {}
        if not isinstance(first, dict):
            return None
        for key in ("big", "c516x688", "c246x328", "tm"):
            value = first.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _extract_card_payload(cls, card: Dict) -> Dict:
        dimensions = card.get("dimensions", {}) or {}
        subject_id = (
            card.get("subjectID")
            or card.get("subjectId")
            or (card.get("subject") or {}).get("id")
            or (card.get("object") or {}).get("id")
        )
        subject_name = (
            card.get("subjectName")
            or (card.get("subject") or {}).get("name")
            or (card.get("object") or {}).get("name")
        )
        return {
            "nm_id": card.get("nmID"),
            "imt_id": card.get("imtID") or card.get("imtId"),
            "vendor_code": card.get("vendorCode"),
            "title": card.get("title"),
            "brand": card.get("brand"),
            "subject_id": subject_id,
            "subject_name": subject_name,
            "photo_url": cls._extract_first_photo_url(card),
            "weight_kg": cls._to_float(dimensions.get("weightBrutto"), default=0.0),
            "length_cm": cls._to_float(dimensions.get("length"), default=0.0),
            "width_cm": cls._to_float(dimensions.get("width"), default=0.0),
            "height_cm": cls._to_float(dimensions.get("height"), default=0.0),
            "volume_liters": cls._calc_volume_liters(dimensions),
            "wb_created_at": card.get("createdAt"),
            "wb_updated_at": card.get("updatedAt"),
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
        seen_cursors: set[tuple] = set()
        pages = 0
        max_pages = 5000

        while total == limit:
            pages += 1
            if pages > max_pages:
                raise Exception("WB Content API pagination exceeded safe page limit")
            response = _request_with_retry(
                "POST",
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
            cursor_key = (cursor.get("updatedAt"), cursor.get("nmID"), total)
            if cursor_key in seen_cursors:
                break
            seen_cursors.add(cursor_key)

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
        response = _request_with_retry(
            "GET",
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

        response = _request_with_retry(
            "GET",
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

    def get_category_commissions(self, locale: str = "ru") -> Dict:
        url = "https://common-api.wildberries.ru/api/v1/tariffs/commission"
        response = _request_with_retry(
            "GET",
            url,
            headers=self.headers,
            params={"locale": locale},
            timeout=30,
        )
        if response.status_code != 200:
            raise Exception(f"WB Common API Error {response.status_code}:\n{response.text}")
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"report": []}


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
        response = _request_with_retry(
            "GET",
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


class WBFinanceReportsClient:
    """
    Клиент для Finance API: детализация отчётов реализации.
    """

    BASE_URL = "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed"

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
        response = _request_with_retry(
            "POST",
            self.BASE_URL,
            headers=self.headers,
            json={
                "dateFrom": date_from,
                "dateTo": date_to,
                "limit": limit,
                "rrdId": rrdid,
                "period": period,
            },
            timeout=61,
        )
        if response.status_code == 204:
            return (204, [])
        if response.status_code != 200:
            raise Exception(f"WB ReportDetail API Error {response.status_code}:\n{response.text}")

        payload = response.json()
        if isinstance(payload, list):
            return (200, payload)
        return (200, [])


# Backward compatibility alias.
WBStatisticsReportsClient = WBFinanceReportsClient


class WBDiscountsPricesClient:
    """
    Клиент для Discounts & Prices API (цены/скидки по размерам товара).
    """

    BASE_URL = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/size/nm"
    FILTER_URL = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json",
        }

    def get_goods_size_prices(self, nm_id: int, limit: int = 1000, offset: int = 0) -> Dict:
        response = _request_with_retry(
            "GET",
            self.BASE_URL,
            headers=self.headers,
            params={
                "nmID": int(nm_id),
                "limit": int(limit),
                "offset": int(offset),
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise Exception(f"WB Discounts Prices API Error {response.status_code}: {response.text}")

        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"data": {"listGoods": []}}

    def get_goods_with_prices(self, limit: int = 1000, offset: int = 0) -> Dict:
        """
        Массовая выгрузка товаров с ценами/скидками.
        Для получения всех товаров:
        - limit=1000
        - offset увеличивать на предыдущий limit до пустого listGoods.
        """
        response = _request_with_retry(
            "GET",
            self.FILTER_URL,
            headers=self.headers,
            params={
                "limit": int(limit),
                "offset": int(offset),
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise Exception(f"WB Discounts Prices API Error {response.status_code}: {response.text}")

        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"data": {"listGoods": []}}


class WBPromotionClient:
    """
    Клиент для WB Promotion API (рекламные кампании и их статистика).
    """

    BASE_URL = "https://advert-api.wildberries.ru"

    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json",
        }

    def _get_campaigns_count(self) -> Dict:
        response = _request_with_retry(
            "GET",
            f"{self.BASE_URL}/adv/v1/promotion/count",
            headers=self.headers,
            attempts=1,
            backoff_seconds=0,
            timeout=60,
        )
        if response.status_code != 200:
            raise Exception(f"WB Promotion Count API Error {response.status_code}: {response.text}")
        data = response.json()
        if isinstance(data, dict):
            return data
        return {"adverts": []}

    def _get_campaigns_info(
        self,
        *,
        ids: List[int] | None = None,
        statuses: List[int] | None = None,
        payment_type: str | None = None,
    ) -> List[Dict]:
        params: Dict[str, str] = {}
        if ids:
            params["ids"] = ",".join(str(int(x)) for x in ids[:50])
        if statuses:
            params["statuses"] = ",".join(str(int(x)) for x in statuses)
        if payment_type:
            params["payment_type"] = payment_type

        response = _request_with_retry(
            "GET",
            f"{self.BASE_URL}/api/advert/v2/adverts",
            headers=self.headers,
            params=params,
            attempts=1,
            backoff_seconds=0,
            timeout=60,
        )
        if response.status_code != 200:
            raise Exception(f"WB Promotion Campaigns Info API Error {response.status_code}: {response.text}")
        data = response.json()
        if isinstance(data, dict):
            adverts = data.get("adverts")
            if isinstance(adverts, list):
                return adverts
        if isinstance(data, list):
            return data
        return []

    def list_adverts(self, statuses: List[int] | None = None, advert_type: int | None = None) -> List[Dict]:
        """
        Получить список рекламных кампаний.
        """
        # По актуальной документации:
        # 1) /adv/v1/promotion/count — получаем список advertId;
        # 2) /api/advert/v2/adverts — получаем детальную информацию по ID.
        count_payload = self._get_campaigns_count()
        grouped = count_payload.get("adverts") if isinstance(count_payload, dict) else []
        advert_ids: List[int] = []
        fallback_rows: List[Dict] = []
        if isinstance(grouped, list):
            for group in grouped:
                if not isinstance(group, dict):
                    continue
                group_type = group.get("type")
                group_status = group.get("status")
                if advert_type is not None and int(group_type or -999) != int(advert_type):
                    continue
                if statuses and int(group_status or -999) not in {int(x) for x in statuses}:
                    continue
                for advert in (group.get("advert_list") or []):
                    if not isinstance(advert, dict):
                        continue
                    advert_id = advert.get("advertId")
                    try:
                        advert_id_int = int(advert_id)
                    except (TypeError, ValueError):
                        continue
                    advert_ids.append(advert_id_int)
                    fallback_rows.append(
                        {
                            "id": advert_id_int,
                            "status": int(group_status) if group_status is not None else None,
                            "type": int(group_type) if group_type is not None else None,
                            "changeTime": advert.get("changeTime"),
                        }
                    )

        if not advert_ids:
            return []

        detailed_rows: List[Dict] = []
        for idx in range(0, len(advert_ids), 50):
            ids_chunk = advert_ids[idx: idx + 50]
            rows = self._get_campaigns_info(ids=ids_chunk)
            if isinstance(rows, list):
                detailed_rows.extend(rows)

        if detailed_rows:
            fallback_by_id: Dict[int, Dict] = {}
            for row in fallback_rows:
                try:
                    row_id = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                fallback_by_id[row_id] = row

            merged_rows: List[Dict] = []
            for row in detailed_rows:
                if not isinstance(row, dict):
                    continue
                advert_id = row.get("id", row.get("advertId"))
                try:
                    advert_id_int = int(advert_id)
                except (TypeError, ValueError):
                    merged_rows.append(row)
                    continue

                fallback = fallback_by_id.get(advert_id_int) or {}
                merged = dict(row)
                if merged.get("type") in (None, "", 0) and fallback.get("type") is not None:
                    merged["type"] = fallback.get("type")
                if merged.get("status") in (None, "", 0) and fallback.get("status") is not None:
                    merged["status"] = fallback.get("status")
                if not merged.get("changeTime") and fallback.get("changeTime"):
                    merged["changeTime"] = fallback.get("changeTime")
                merged_rows.append(merged)
            return merged_rows
        return fallback_rows

    def get_fullstats(self, advert_ids: List[int], date_from: str, date_to: str) -> List[Dict]:
        """
        Получить статистику кампаний за период.
        Лимит WB: до 31 дня и до 50 advert IDs за запрос.
        """
        if not advert_ids:
            return []

        normalized_ids = [int(x) for x in advert_ids]
        ids_csv = ",".join(str(x) for x in normalized_ids)

        params = [("ids", ids_csv), ("beginDate", date_from), ("endDate", date_to)]
        response = _request_with_retry(
            "GET",
            f"{self.BASE_URL}/adv/v3/fullstats",
            headers=self.headers,
            params=params,
            attempts=1,
            backoff_seconds=0,
            timeout=90,
        )
        if response.status_code != 200:
            raise Exception(f"WB Promotion Stats API Error {response.status_code}: {response.text}")
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return []
