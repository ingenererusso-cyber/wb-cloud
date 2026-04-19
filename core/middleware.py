from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse


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

