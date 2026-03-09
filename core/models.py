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

class Order(models.Model):
    seller = models.ForeignKey(SellerAccount, on_delete=models.CASCADE)

    srid = models.CharField(max_length=255, unique=True)

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
