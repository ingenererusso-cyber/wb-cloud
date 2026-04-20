from __future__ import annotations

import logging
import traceback
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render


logger = logging.getLogger("mp_saas")


class ApiAuthRedirectMiddleware:
    """
    Для API-запросов возвращает JSON 401 вместо HTML-редиректа на login,
    чтобы фронтенд мог корректно отправить пользователя на авторизацию
    и потом вернуть обратно на текущую страницу.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if getattr(request, "user", None) and request.user.is_authenticated:
            return response

        if not request.path.startswith("/api/"):
            return response

        if response.status_code not in (301, 302):
            return response

        location = response.headers.get("Location", "") or ""
        login_url = str(getattr(settings, "LOGIN_URL", "/login/") or "/login/")
        login_path = login_url if login_url.startswith("/") else "/login/"

        if "/login" not in location and not location.startswith(login_path):
            return response

        return JsonResponse(
            {
                "error": "auth_required",
                "code": "auth_required",
                "login_url": login_path,
                "redirect_url": f"{login_path}?next={request.get_full_path()}",
            },
            status=401,
        )


class GlobalExceptionCaptureMiddleware:
    """
    Глобальный перехват необработанных исключений:
    - API: возвращаем JSON без traceback;
    - UI: возвращаем аккуратную 500-страницу.
    Полные детали пишем в логи и AppErrorLog.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except Exception as exc:
            if settings.DEBUG:
                raise

            request_id = uuid.uuid4().hex[:12]
            self._log_exception(request, exc, request_id)

            if request.path.startswith("/api/"):
                return JsonResponse(
                    {
                        "error": "internal_error",
                        "code": "internal_error",
                        "message": "Внутренняя ошибка сервера. Попробуйте позже.",
                        "request_id": request_id,
                    },
                    status=500,
                )

            return render(
                request,
                "errors/500.html",
                {"request_id": request_id},
                status=500,
            )

    def _log_exception(self, request, exc: Exception, request_id: str) -> None:
        tb = traceback.format_exc()
        logger.exception(
            "Unhandled exception (request_id=%s, path=%s, user_id=%s): %s",
            request_id,
            request.path,
            getattr(getattr(request, "user", None), "id", None),
            exc,
        )

        try:
            from core.models import AppErrorLog, SellerAccount
            user = getattr(request, "user", None)
            seller = None
            if getattr(user, "is_authenticated", False):
                try:
                    seller = SellerAccount.objects.filter(user=user).first()
                except Exception:
                    seller = None

            AppErrorLog.objects.create(
                source="middleware.unhandled",
                message=f"Unhandled exception: {exc}",
                user=user if getattr(user, "is_authenticated", False) else None,
                seller=seller,
                path=request.path,
                context_json={
                    "request_id": request_id,
                    "method": request.method,
                    "full_path": request.get_full_path(),
                },
                traceback_text=tb,
            )
        except Exception:
            # Не даем логированию ломать обработку ответа.
            logger.exception("Failed to persist AppErrorLog for request_id=%s", request_id)
