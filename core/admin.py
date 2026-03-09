from django.contrib import admin
from .models import SellerAccount, Product, WarehouseStockDetailed, Order, WbOffice


@admin.register(SellerAccount)
class SellerAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user")
    search_fields = ("name", "user__username", "user__email")


admin.site.register(Product)
admin.site.register(WarehouseStockDetailed)
admin.site.register(Order)
admin.site.register(WbOffice)
