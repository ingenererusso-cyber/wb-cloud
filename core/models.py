from django.contrib.auth.models import User
from django.db import models
from core.security import decrypt_secret, encrypt_secret, mask_secret


class SellerAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name="seller_account")
    name = models.CharField(max_length=255)
    api_token = models.TextField(blank=True, default="")

    @property
    def api_token_plain(self) -> str:
        return decrypt_secret(self.api_token)

    @property
    def has_api_token(self) -> bool:
        return bool(self.api_token_plain.strip())

    @property
    def api_token_masked(self) -> str:
        return mask_secret(self.api_token_plain)

    def set_api_token(self, value: str | None) -> None:
        self.api_token = encrypt_secret(value)


class UnitEconomicsSettings(models.Model):
    seller = models.OneToOneField(SellerAccount, on_delete=models.CASCADE, related_name="unit_economics_settings")
    assumed_spp_percent = models.FloatField(default=25.0)
    drr_percent = models.FloatField(default=10.0)
    defect_percent = models.FloatField(default=1.0)
    acquiring_percent = models.FloatField(default=2.5)
    acceptance_cost_per_liter = models.FloatField(default=1.7)
    fulfillment_cost_per_order = models.FloatField(default=0.0)
    usn_percent = models.FloatField(default=6.0)
    vat_percent = models.FloatField(default=0.0)
    updated_at = models.DateTimeField(auto_now=True)


class Product(models.Model):
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    nm_id = models.BigIntegerField()
    vendor_code = models.CharField(max_length=255)
    title = models.CharField(max_length=500, null=True, blank=True)
    brand = models.CharField(max_length=255, null=True, blank=True)
    subject_id = models.BigIntegerField(null=True, blank=True)
    subject_name = models.CharField(max_length=255, null=True, blank=True)
    photo_url = models.URLField(max_length=1000, null=True, blank=True)
    weight_kg = models.FloatField(null=True, blank=True)
    length_cm = models.FloatField(null=True, blank=True)
    width_cm = models.FloatField(null=True, blank=True)
    height_cm = models.FloatField(null=True, blank=True)
    volume_liters = models.FloatField(null=True, blank=True)
    purchase_price = models.FloatField(null=True, blank=True)
    wb_created_at = models.DateTimeField(null=True, blank=True)
    wb_updated_at = models.DateTimeField(null=True, blank=True)


class ProductUnitEconomicsCalculation(models.Model):
    """
    Последний сохраненный расчет юнит-экономики по карточке товара.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    input_data = models.JSONField(default=dict, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    net_profit = models.FloatField(default=0.0)
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "product")]
        indexes = [
            models.Index(fields=["seller", "product"]),
            models.Index(fields=["seller", "calculated_at"]),
        ]


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


class SellerWarehouse(models.Model):
    """
    Склады продавца из WB Marketplace API /api/v3/warehouses.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    seller_warehouse_id = models.BigIntegerField()
    office_id = models.BigIntegerField(null=True, blank=True)
    name = models.CharField(max_length=255)
    cargo_type = models.IntegerField(null=True, blank=True)
    delivery_type = models.IntegerField(null=True, blank=True)
    is_deleting = models.BooleanField(default=False)
    is_processing = models.BooleanField(default=False)
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "seller_warehouse_id")]
        indexes = [
            models.Index(fields=["seller", "name"]),
            models.Index(fields=["seller", "office_id"]),
            models.Index(fields=["seller", "updated_at"]),
        ]


class ProductCardSize(models.Model):
    """
    Размер товара из карточки WB Content API.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    chrt_id = models.BigIntegerField()
    nm_id = models.BigIntegerField(null=True, blank=True)
    vendor_code = models.CharField(max_length=255, null=True, blank=True)
    title = models.CharField(max_length=500, null=True, blank=True)
    tech_size = models.CharField(max_length=100, null=True, blank=True)
    wb_size = models.CharField(max_length=100, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "chrt_id")]
        indexes = [
            models.Index(fields=["seller", "vendor_code"]),
            models.Index(fields=["seller", "nm_id"]),
        ]


class ProductSizePrice(models.Model):
    """
    Цены и скидки по размерам товара из Discounts & Prices API.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    nm_id = models.BigIntegerField()
    size_id = models.BigIntegerField()
    chrt_id = models.BigIntegerField(null=True, blank=True)
    vendor_code = models.CharField(max_length=255, null=True, blank=True)
    tech_size_name = models.CharField(max_length=100, null=True, blank=True)

    price = models.FloatField(null=True, blank=True)
    discounted_price = models.FloatField(null=True, blank=True)
    club_discounted_price = models.FloatField(null=True, blank=True)
    currency_iso_code_4217 = models.CharField(max_length=16, null=True, blank=True)
    discount_percent = models.FloatField(null=True, blank=True)
    club_discount_percent = models.FloatField(null=True, blank=True)

    editable_size_price = models.BooleanField(default=False)
    is_bad_turnover = models.BooleanField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "nm_id", "size_id")]
        indexes = [
            models.Index(fields=["seller", "nm_id"]),
            models.Index(fields=["seller", "vendor_code"]),
            models.Index(fields=["seller", "updated_at"]),
        ]


class SellerFbsStock(models.Model):
    """
    Остатки FBS по складам продавца и размерам (chrtId).
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    seller_warehouse = models.ForeignKey(SellerWarehouse, on_delete=models.CASCADE)
    warehouse_name = models.CharField(max_length=255)
    chrt_id = models.BigIntegerField()
    amount = models.IntegerField(default=0)
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "seller_warehouse", "chrt_id")]
        indexes = [
            models.Index(fields=["seller", "warehouse_name"]),
            models.Index(fields=["seller", "chrt_id"]),
            models.Index(fields=["seller", "updated_at"]),
        ]


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


class WbCategoryCommission(models.Model):
    """
    Комиссия WB по категориям товаров (по предметам subjectID).
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    locale = models.CharField(max_length=10, default="ru")
    subject_id = models.BigIntegerField()
    subject_name = models.CharField(max_length=255, null=True, blank=True)
    parent_id = models.BigIntegerField(null=True, blank=True)
    parent_name = models.CharField(max_length=255, null=True, blank=True)

    kgvp_booking = models.FloatField(null=True, blank=True)
    kgvp_marketplace = models.FloatField(null=True, blank=True)
    kgvp_pickup = models.FloatField(null=True, blank=True)
    kgvp_supplier = models.FloatField(null=True, blank=True)
    kgvp_supplier_express = models.FloatField(null=True, blank=True)
    paid_storage_kgvp = models.FloatField(null=True, blank=True)  # FBW

    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "locale", "subject_id")]
        indexes = [
            models.Index(fields=["seller", "subject_id"]),
            models.Index(fields=["seller", "updated_at"]),
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
    Детализация отчета реализации WB.
    Текущий источник синхронизации: finance-api /api/finance/v1/sales-reports/detailed.
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


class WbAdvertCampaign(models.Model):
    """
    Рекламная кампания WB (Promotion API).
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    advert_id = models.BigIntegerField()
    campaign_name = models.CharField(max_length=500, null=True, blank=True)
    advert_type = models.IntegerField(null=True, blank=True)
    status = models.IntegerField(null=True, blank=True)
    create_time = models.DateTimeField(null=True, blank=True)
    change_time = models.DateTimeField(null=True, blank=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    daily_budget = models.FloatField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "advert_id")]
        indexes = [
            models.Index(fields=["seller", "advert_id"]),
            models.Index(fields=["seller", "status"]),
            models.Index(fields=["seller", "updated_at"]),
        ]


class WbAdvertStatDaily(models.Model):
    """
    Дневная статистика рекламы WB (Campaigns Statistics /adv/v3/fullstats),
    включая распределение затрат по артикулам.
    """

    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)
    advert_id = models.BigIntegerField()
    stat_date = models.DateField()
    nm_id = models.BigIntegerField(null=True, blank=True)

    spend = models.FloatField(default=0.0)
    views = models.IntegerField(null=True, blank=True)
    clicks = models.IntegerField(null=True, blank=True)
    orders = models.IntegerField(null=True, blank=True)
    add_to_cart = models.IntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("seller", "advert_id", "stat_date", "nm_id")]
        indexes = [
            models.Index(fields=["seller", "stat_date"]),
            models.Index(fields=["seller", "nm_id", "stat_date"]),
            models.Index(fields=["seller", "advert_id", "stat_date"]),
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
