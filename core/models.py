from django.contrib.auth.models import User
from django.db import models


class SellerAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name="seller_account")
    name = models.CharField(max_length=255)
    api_token = models.CharField(max_length=500)


class Product(models.Model):
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    nm_id = models.BigIntegerField()
    vendor_code = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, null=True, blank=True)
    weight_kg = models.FloatField(null=True, blank=True)
    volume_liters = models.FloatField(null=True, blank=True)


class Order(models.Model):
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)

    srid = models.CharField(max_length=255)

    nm_id = models.BigIntegerField()
    supplier_article = models.CharField(max_length=255)
    tech_size = models.CharField(max_length=100)

    warehouse_name = models.CharField(max_length=255)
    warehouse_type = models.CharField(max_length=50)
    country_name = models.CharField(max_length=255, null=True, blank=True)
    oblast_okrug_name = models.CharField(max_length=255, null=True, blank=True)
    region_name = models.CharField(max_length=255, null=True, blank=True)

    is_cancel = models.BooleanField(default=False)

    finished_price = models.FloatField(null=True, blank=True)

    order_date = models.DateTimeField()
    last_change_date = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)
    is_local = models.BooleanField(default=False)

    class Meta:
        unique_together = [("seller", "srid")]
        indexes = [
            models.Index(fields=["nm_id"]),
            models.Index(fields=["warehouse_name"]),
            models.Index(fields=["region_name"]),
        ]

class WarehouseStockDetailed(models.Model):
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    nm_id = models.BigIntegerField()
    supplier_article = models.CharField(max_length=255)
    tech_size = models.CharField(max_length=100)
    warehouse_name = models.CharField(max_length=255)
    quantity = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["nm_id"]),
            models.Index(fields=["warehouse_name"]),
        ]

class WbOffice(models.Model):
    office_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    address = models.TextField()
    federal_district = models.CharField(max_length=255, null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.name


class WbWarehouseTariff(models.Model):
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    warehouse_name = models.CharField(max_length=255)
    geo_name = models.CharField(max_length=255, null=True, blank=True)
    tariff_date = models.DateField()

    box_delivery_base = models.FloatField(null=True, blank=True)
    box_delivery_coef_expr = models.FloatField(null=True, blank=True)
    box_delivery_liter = models.FloatField(null=True, blank=True)
    box_delivery_marketplace_base = models.FloatField(null=True, blank=True)
    box_delivery_marketplace_coef_expr = models.FloatField(null=True, blank=True)
    box_delivery_marketplace_liter = models.FloatField(null=True, blank=True)
    box_storage_base = models.FloatField(null=True, blank=True)
    box_storage_coef_expr = models.FloatField(null=True, blank=True)
    box_storage_liter = models.FloatField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "warehouse_name", "tariff_date")]
        indexes = [
            models.Index(fields=["seller", "tariff_date"]),
            models.Index(fields=["warehouse_name"]),
        ]


class TransitDirectionTariff(models.Model):
    """
    Тариф направления: от транзитного склада к складу/региону назначения.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE, null=True, blank=True)
    transit_warehouse = models.CharField(max_length=255)
    target_warehouse = models.CharField(max_length=255, null=True, blank=True)
    target_region = models.CharField(max_length=255, null=True, blank=True)

    tariff_per_pallet = models.FloatField(null=True, blank=True)
    box_price_per_liter_lt_1500 = models.FloatField(null=True, blank=True)
    box_price_per_liter_gt_1500 = models.FloatField(null=True, blank=True)
    delivery_eta = models.CharField(max_length=120, null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["transit_warehouse"]),
            models.Index(fields=["target_region"]),
            models.Index(fields=["seller"]),
        ]


class WbAcceptanceCoefficient(models.Model):
    """
    Тарифы приёмки поставок WB (acceptance/coefficients) по складам и датам.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    coeff_date = models.DateField()
    warehouse_id = models.BigIntegerField()
    warehouse_name = models.CharField(max_length=255, null=True, blank=True)
    box_type_id = models.IntegerField(null=True, blank=True)

    coefficient = models.FloatField(null=True, blank=True)
    allow_unload = models.BooleanField(default=False)
    is_sorting_center = models.BooleanField(default=False)

    storage_coef = models.FloatField(null=True, blank=True)
    delivery_coef = models.FloatField(null=True, blank=True)
    delivery_base_liter = models.FloatField(null=True, blank=True)
    delivery_additional_liter = models.FloatField(null=True, blank=True)
    storage_base_liter = models.FloatField(null=True, blank=True)
    storage_additional_liter = models.FloatField(null=True, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "coeff_date", "warehouse_id", "box_type_id")]
        indexes = [
            models.Index(fields=["seller", "coeff_date"]),
            models.Index(fields=["warehouse_id"]),
            models.Index(fields=["warehouse_name"]),
            models.Index(fields=["box_type_id"]),
        ]


class RealizationReportDetail(models.Model):
    """
    Детализация отчета реализации WB (reportDetailByPeriod).
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    rrd_id = models.BigIntegerField()

    realizationreport_id = models.BigIntegerField(null=True, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    create_dt = models.DateField(null=True, blank=True)

    srid = models.CharField(max_length=255, null=True, blank=True)
    nm_id = models.BigIntegerField(null=True, blank=True)
    sa_name = models.CharField(max_length=255, null=True, blank=True)
    office_name = models.CharField(max_length=255, null=True, blank=True)
    site_country = models.CharField(max_length=255, null=True, blank=True)
    bonus_type_name = models.CharField(max_length=255, null=True, blank=True)
    supplier_oper_name = models.CharField(max_length=255, null=True, blank=True)
    doc_type_name = models.CharField(max_length=120, null=True, blank=True)

    order_dt = models.DateTimeField(null=True, blank=True)
    sale_dt = models.DateTimeField(null=True, blank=True)
    rr_dt = models.DateField(null=True, blank=True)
    fix_tariff_date_from = models.DateField(null=True, blank=True)
    fix_tariff_date_to = models.DateField(null=True, blank=True)

    quantity = models.IntegerField(null=True, blank=True)
    delivery_rub = models.FloatField(null=True, blank=True)
    dlv_prc = models.FloatField(null=True, blank=True)
    storage_fee = models.FloatField(null=True, blank=True)
    deduction = models.FloatField(null=True, blank=True)
    acceptance = models.FloatField(null=True, blank=True)
    rebill_logistic_cost = models.FloatField(null=True, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "rrd_id")]
        indexes = [
            models.Index(fields=["seller", "rr_dt"]),
            models.Index(fields=["seller", "srid"]),
            models.Index(fields=["seller", "office_name"]),
        ]


class SyncTask(models.Model):
    """
    Фоновая задача синхронизации данных на главной.

    Хранится в БД, чтобы статус был доступен из любого воркера.
    """

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_ERROR, "Error"),
    ]

    task_id = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    progress = models.PositiveSmallIntegerField(default=0)
    step = models.CharField(max_length=255, null=True, blank=True)
    message = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["task_id"]),
        ]


class TesterFeedback(models.Model):
    CATEGORY_BUG = "bug"
    CATEGORY_CALC = "calc"
    CATEGORY_SYNC = "sync"
    CATEGORY_IDEA = "idea"
    CATEGORY_CHOICES = [
        (CATEGORY_BUG, "Баг интерфейса"),
        (CATEGORY_CALC, "Неточность расчета"),
        (CATEGORY_SYNC, "Проблема синхронизации"),
        (CATEGORY_IDEA, "Идея/улучшение"),
    ]

    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Низкий"),
        (PRIORITY_MEDIUM, "Средний"),
        (PRIORITY_HIGH, "Высокий"),
    ]

    STATUS_NEW = "new"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_FIXED = "fixed"
    STATUS_WONTFIX = "wontfix"
    STATUS_CHOICES = [
        (STATUS_NEW, "Новая"),
        (STATUS_IN_PROGRESS, "В работе"),
        (STATUS_FIXED, "Исправлено"),
        (STATUS_WONTFIX, "Не планируется"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seller = models.ForeignKey(SellerAccount, on_delete=models.SET_NULL, null=True, blank=True)
    page_url = models.CharField(max_length=500, blank=True, default="")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_BUG)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    message = models.TextField()
    include_context = models.BooleanField(default=True)
    context_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["category", "priority"]),
            models.Index(fields=["user", "created_at"]),
        ]


class AppErrorLog(models.Model):
    LEVEL_ERROR = "error"
    LEVEL_WARNING = "warning"
    LEVEL_INFO = "info"
    LEVEL_CHOICES = [
        (LEVEL_ERROR, "Error"),
        (LEVEL_WARNING, "Warning"),
        (LEVEL_INFO, "Info"),
    ]

    source = models.CharField(max_length=120)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default=LEVEL_ERROR)
    message = models.TextField()
    path = models.CharField(max_length=500, blank=True, default="")
    traceback_text = models.TextField(blank=True, default="")
    context_json = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    seller = models.ForeignKey(SellerAccount, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["source", "created_at"]),
            models.Index(fields=["level", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]
