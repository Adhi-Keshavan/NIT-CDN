from django.contrib import admin

from .models import (
    BOMLine, Customer, Invoice, Item, Material, Order, OrderLine,
    Payment, Product, ProductionRun, Purchase, StockMovement,
)


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("item", "selling_price")
    inlines = [BOMLineInline]


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "date", "status")
    inlines = [OrderLineInline]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("date", "item", "quantity", "unit", "base_quantity", "source_type", "source_reference")
    list_filter = ("source_type", "date")
    search_fields = ("item__sku", "source_reference")
    readonly_fields = ("base_quantity",)


for model in (Customer, Item, Material, Purchase, ProductionRun, Invoice, Payment):
    admin.site.register(model)
