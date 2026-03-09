from django.contrib import admin
from .models import SellerAccount, Product, WarehouseStockDetailed, Order, WbOffice

admin.site.register(SellerAccount)
admin.site.register(Product)
admin.site.register(WarehouseStockDetailed)
admin.site.register(Order)
admin.site.register(WbOffice)