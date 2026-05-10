from django.conf import settings


def legal_context(_request):
    return {
        "legal_doc_versions": getattr(settings, "LEGAL_DOC_VERSIONS", {}),
        "legal_operator_name": getattr(settings, "LEGAL_OPERATOR_NAME", ""),
        "legal_operator_status": getattr(settings, "LEGAL_OPERATOR_STATUS", ""),
        "legal_operator_inn": getattr(settings, "LEGAL_OPERATOR_INN", ""),
        "legal_operator_ogrn": getattr(settings, "LEGAL_OPERATOR_OGRN", ""),
        "legal_operator_address": getattr(settings, "LEGAL_OPERATOR_ADDRESS", ""),
        "legal_operator_email": getattr(settings, "LEGAL_OPERATOR_EMAIL", ""),
        "legal_operator_phone": getattr(settings, "LEGAL_OPERATOR_PHONE", ""),
        "legal_operator_bank_account": getattr(settings, "LEGAL_OPERATOR_BANK_ACCOUNT", ""),
        "legal_operator_bank_bik": getattr(settings, "LEGAL_OPERATOR_BANK_BIK", ""),
        "legal_operator_bank_name": getattr(settings, "LEGAL_OPERATOR_BANK_NAME", ""),
        "legal_operator_bank_corr": getattr(settings, "LEGAL_OPERATOR_BANK_CORR", ""),
    }
