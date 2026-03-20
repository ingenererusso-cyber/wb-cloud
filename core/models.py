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
