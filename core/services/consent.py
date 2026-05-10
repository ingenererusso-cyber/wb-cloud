from core.models import ConsentLog


def _get_client_ip(request):
    forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[0]
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    return remote_addr or None


def record_consent(
    *,
    request,
    email,
    user=None,
    kind,
    action="grant",
    document_version,
    source,
    document_text_hash="",
):
    return ConsentLog.objects.create(
        user=user,
        email=(email or "").strip().lower(),
        kind=(kind or "").strip(),
        action=(action or "grant").strip(),
        document_version=(document_version or "").strip(),
        document_text_hash=(document_text_hash or "").strip(),
        ip=_get_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512],
        source=(source or "").strip()[:64],
    )
