from django.contrib import admin
from .models import (
    SellerAccount,
    Product,
    WarehouseStockDetailed,
    Order,
    WbOffice,
    WbWarehouseTariff,
    TransitDirectionTariff,
    WbAcceptanceCoefficient,
    RealizationReportDetail,
)


@admin.register(SellerAccount)
class SellerAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user")
    search_fields = ("name", "user__username", "user__email")


admin.site.register(Product)
admin.site.register(WarehouseStockDetailed)
admin.site.register(WbOffice)
admin.site.register(WbWarehouseTariff)
admin.site.register(TransitDirectionTariff)
admin.site.register(WbAcceptanceCoefficient)


@admin.register(RealizationReportDetail)
class RealizationReportDetailAdmin(admin.ModelAdmin):
    list_display = (
        "rrd_id",
        "srid",
        "seller",
        "rr_dt",
        "office_name",
        "bonus_type_name",
        "quantity",
        "delivery_rub",
        "nm_id",
    )
    search_fields = (
        "rrd_id",
        "srid",
        "office_name",
        "sa_name",
        "nm_id",
        "realizationreport_id",
        "seller__name",
        "seller__user__username",
    )
    list_filter = (
        "seller",
        "rr_dt",
        "office_name",
        "site_country",
        "bonus_type_name",
        "doc_type_name",
    )
    ordering = ("-rr_dt", "-rrd_id")
    date_hierarchy = "rr_dt"
    list_select_related = ("seller",)
    list_per_page = 50
    readonly_fields = ("updated_at",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "srid",
        "seller",
        "supplier_article",
        "nm_id",
        "warehouse_name",
        "oblast_okrug_name",
        "country_name",
        "is_local",
        "is_cancel",
        "order_date",
    )
    search_fields = (
        "srid",
        "supplier_article",
        "nm_id",
        "warehouse_name",
        "oblast_okrug_name",
        "region_name",
        "country_name",
        "seller__name",
        "seller__user__username",
    )
    list_filter = (
        "seller",
        "warehouse_type",
        "is_local",
        "is_cancel",
        "country_name",
    )
    ordering = ("-order_date",)
    date_hierarchy = "order_date"
    list_select_related = ("seller",)
    list_per_page = 50
