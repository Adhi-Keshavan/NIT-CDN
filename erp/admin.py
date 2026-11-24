from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError

from .ledger import approve_order, complete_production_run, dispatch_order, post_adjustment, receive_purchase

from .models import (
    BOMLine, Customer, Invoice, InvoiceLine, Item, Material, Order, OrderLine,
    Payment, Product, ProductionConsumption, ProductionCostRevision, ProductionRun, Purchase,
    StockAdjustment, StockAdjustmentLine, StockMovement,
)


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 0


class PostedReadOnlyMixin:
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.stock_movements.exists():
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.stock_movements.exists()) and super().has_delete_permission(request, obj)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("item", "selling_price")
    inlines = [BOMLineInline]


class PostedInlineMixin(PostedReadOnlyMixin):
    def has_add_permission(self, request, obj=None):
        return not (obj and obj.stock_movements.exists()) and super().has_add_permission(request, obj)


class OrderLineInline(PostedInlineMixin, admin.TabularInline):
    model = OrderLine
    extra = 0

    def has_add_permission(self, request, obj=None):
        return (obj is None or obj.status == Order.Status.DRAFT) and super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.status == Order.Status.DRAFT) and super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != Order.Status.DRAFT:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)


class ProductionConsumptionInline(PostedInlineMixin, admin.TabularInline):
    model = ProductionConsumption
    extra = 0


class StockAdjustmentLineInline(PostedInlineMixin, admin.TabularInline):
    model = StockAdjustmentLine
    extra = 0


def post_selected(model_admin, request, queryset, action):
    completed = 0
    for document in queryset:
        try:
            action(document)
            completed += 1
        except ValidationError as exc:
            model_admin.message_user(request, f"{document}: {exc}", level=messages.ERROR)
    if completed:
        model_admin.message_user(request, f"Processed {completed} document(s).", level=messages.SUCCESS)


@admin.register(Order)
class OrderAdmin(PostedReadOnlyMixin, admin.ModelAdmin):
    list_display = ("reference", "customer", "date", "status")
    inlines = [OrderLineInline]
    readonly_fields = ("status", "rejection_reason", "reviewed_by", "reviewed_at")
    actions = ("approve_selected", "dispatch_selected")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != Order.Status.DRAFT:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.status == Order.Status.DRAFT) and super().has_delete_permission(request, obj)

    @admin.action(description="Approve selected orders after stock check")
    def approve_selected(self, request, queryset):
        post_selected(self, request, queryset, lambda order: approve_order(order, reviewed_by=request.user))

    @admin.action(description="Dispatch selected orders and post stock")
    def dispatch_selected(self, request, queryset):
        post_selected(self, request, queryset, dispatch_order)


@admin.register(Purchase)
class PurchaseAdmin(PostedReadOnlyMixin, admin.ModelAdmin):
    list_display = ("reference", "material", "quantity", "unit", "date", "status")
    readonly_fields = ("status",)
    actions = ("receive_selected",)

    @admin.action(description="Receive selected purchases and post stock")
    def receive_selected(self, request, queryset):
        post_selected(self, request, queryset, receive_purchase)


@admin.register(ProductionRun)
class ProductionRunAdmin(PostedReadOnlyMixin, admin.ModelAdmin):
    list_display = ("reference", "product", "planned_quantity", "completed_quantity", "date", "status", "material_cost", "cost_per_packet", "yield_variance_alert")
    readonly_fields = ("status",)
    inlines = [ProductionConsumptionInline]
    actions = ("complete_selected",)

    @admin.action(description="Complete selected runs and post stock")
    def complete_selected(self, request, queryset):
        post_selected(self, request, queryset, complete_production_run)


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(PostedReadOnlyMixin, admin.ModelAdmin):
    list_display = ("reference", "date", "kind")
    inlines = [StockAdjustmentLineInline]
    actions = ("post_selected_adjustments",)

    @admin.action(description="Post selected stock adjustments")
    def post_selected_adjustments(self, request, queryset):
        post_selected(self, request, queryset, post_adjustment)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("date", "item", "quantity", "unit", "base_quantity", "source_type", "source_reference")
    list_filter = ("source_type", "date")
    search_fields = ("item__sku", "source_reference")
    readonly_fields = ("base_quantity",)

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return tuple(field.name for field in self.model._meta.fields)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductionCostRevision)
class ProductionCostRevisionAdmin(admin.ModelAdmin):
    list_display = ("run", "changed_by", "changed_at", "old_labour_cost", "new_labour_cost", "old_power_cost", "new_power_cost")

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class IssuedDocumentAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(IssuedDocumentAdmin):
    list_display = ("number", "date", "order", "taxable_value", "amount", "balance_due")


@admin.register(InvoiceLine)
class InvoiceLineAdmin(IssuedDocumentAdmin):
    list_display = ("invoice", "line_number", "description", "hsn_code", "taxable_value")


@admin.register(Payment)
class PaymentAdmin(IssuedDocumentAdmin):
    list_display = ("reference", "invoice", "date", "amount")


for model in (Customer, Item, Material):
    admin.site.register(model)
