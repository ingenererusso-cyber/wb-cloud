# WB support package: nm_id 399293511 (MCS-02)

## 1) Methods used in our calculations
- Sales report details: `GET https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod`
- Warehouse box tariffs: `GET https://common-api.wildberries.ru/api/v1/tariffs/box?date=YYYY-MM-DD`
- Product dimensions/volume source (for card sync): `POST https://content-api.wildberries.ru/content/v2/get/cards/list`

## 2) cURL requests (replace `<TOKEN>` with your API key)
```bash
curl --request GET 'https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod?dateFrom=2025-09-15&dateTo=2026-03-08&period=weekly&limit=100000&rrdid=0' \
  --header 'Authorization: <TOKEN>' \
  --header 'accept: application/json'

curl --request GET 'https://common-api.wildberries.ru/api/v1/tariffs/box?date=2026-03-02' \
  --header 'Authorization: <TOKEN>' \
  --header 'accept: application/json'

curl --request POST 'https://content-api.wildberries.ru/content/v2/get/cards/list' \
  --header 'Authorization: <TOKEN>' \
  --header 'accept: application/json' \
  --header 'Content-Type: application/json' \
  --data '{"settings":{"cursor":{"limit":100},"filter":{"withPhoto":-1}}}'
```

## 3) Observed data for this case
- Rows used (non-MP, "К клиенту при продаже"): **47**
- fact/theory mean: **1.548612**
- fact/theory median: **1.588143**
- fact/theory min: **0.850154**
- fact/theory max: **1.623943**

## 4) Attached files
- Full rows CSV: `exports/wb_support_case_399293511_rows.csv`
- Sample raw payloads + tariffs JSON: `exports/wb_support_case_399293511_samples.json`