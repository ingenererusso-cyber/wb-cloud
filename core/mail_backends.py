import ssl

from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPEmailBackend


class LenientSMTPEmailBackend(DjangoSMTPEmailBackend):
    """
    SMTP backend с отключенной проверкой сертификата.

    Использовать только когда внешний SMTP сервер отдает цепочку сертификатов,
    которую текущее окружение не может провалидировать стандартным trust store.
    """

    @property
    def ssl_context(self):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
