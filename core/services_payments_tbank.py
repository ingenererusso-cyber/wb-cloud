from __future__ import annotations

import hashlib
from typing import Any

import requests
from django.conf import settings


TBANK_STATUS_TO_PAYMENT_STATUS = {
    "NEW": "pending",
    "FORM_SHOWED": "pending",
    "DEADLINE_EXPIRED": "canceled",
    "REJECTED": "rejected",
    "AUTH_FAIL": "rejected",
    "AUTHORIZED": "authorized",
    "CONFIRMED": "confirmed",
    "REVERSED": "canceled",
    "REFUNDED": "canceled",
    "CANCELED": "canceled",
}


class TBankError(Exception):
    pass


def _root_scalar_fields(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in payload.items():
        if key == "Token":
            continue
        if isinstance(value, (dict, list, tuple, set)):
            continue
        if value is None:
            continue
        result[str(key)] = str(value)
    return result


def build_tbank_token(payload: dict[str, Any], password: str) -> str:
    parts = _root_scalar_fields(payload)
    parts["Password"] = str(password)
    raw = "".join(parts[key] for key in sorted(parts))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_tbank_token(payload: dict[str, Any], password: str) -> bool:
    token = str(payload.get("Token") or "").strip().lower()
    if not token:
        return False
    return build_tbank_token(payload, password).lower() == token


def build_init_payload(
    *,
    terminal_key: str,
    password: str,
    order_id: str,
    amount_kopeks: int,
    description: str,
    notification_url: str,
    customer_key: str,
    data: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "TerminalKey": terminal_key,
        "Amount": int(amount_kopeks),
        "OrderId": order_id,
        "Description": description[:140],
        "PayType": "O",
        "Language": "ru",
        "NotificationURL": notification_url,
        "CustomerKey": customer_key[:36],
    }
    if data:
        payload["DATA"] = data
    payload["Token"] = build_tbank_token(payload, password)
    return payload


def init_payment(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(getattr(settings, "TBANK_INIT_URL", "") or "").strip()
    if not url:
        raise TBankError("Не настроен URL инициализации T-Bank.")
    try:
        response = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as exc:
        raise TBankError(f"Не удалось связаться с T-Bank: {exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise TBankError("T-Bank вернул некорректный JSON.") from exc
    if response.status_code >= 400:
        raise TBankError(data.get("Message") or data.get("Details") or "T-Bank вернул ошибку инициализации.")
    if not data.get("Success"):
        raise TBankError(data.get("Message") or data.get("Details") or "T-Bank не принял запрос на оплату.")
    return data


def map_tbank_status(status: str) -> str:
    return TBANK_STATUS_TO_PAYMENT_STATUS.get((status or "").strip().upper(), "pending")
