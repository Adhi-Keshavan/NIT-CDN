from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from .units import Unit, convert_quantity


class Customer(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    state = models.CharField(max_length=50, blank=True)
    state_code = models.CharField(max_length=2, blank=True)
    gstin = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return f"{self.code} — {self.name}"


class Item(models.Model):
    class Kind(models.TextChoices):
        PRODUCT = "product", "Product"
        MATERIAL = "material", "Material"

    sku = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    base_unit = models.CharField(max_length=6, choices=Unit.choices)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sku} — {self.name}"


class Product(models.Model):
    item = models.OneToOneField(Item, on_delete=models.PROTECT, related_name="product")
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    expected_yield_per_batch = models.PositiveIntegerField(default=1, help_text="Expected finished units from one BOM batch")
    packets_per_carton = models.PositiveIntegerField(null=True, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    hsn_code = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(selling_price__gte=0), name="nonnegative_selling_price"),
            models.CheckConstraint(condition=models.Q(expected_yield_per_batch__gt=0), name="positive_batch_yield"),
            models.CheckConstraint(condition=models.Q(gst_rate__gte=0), name="nonnegative_gst_rate"),
        ]

    def clean(self):
        if self.item_id and self.item.kind != Item.Kind.PRODUCT:
            raise ValidationError({"item": "The item must have product kind."})

    def __str__(self):
        return str(self.item)


class Material(models.Model):
    class Category(models.TextChoices):
        RAW = "raw", "Raw material"
        PACKAGING = "packaging", "Packaging material"

    item = models.OneToOneField(Item, on_delete=models.PROTECT, related_name="material")
    standard_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category = models.CharField(max_length=9, choices=Category.choices, default=Category.RAW)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(standard_cost__gte=0), name="nonnegative_standard_cost")]

    def clean(self):
        if self.item_id and self.item.kind != Item.Kind.MATERIAL:
            raise ValidationError({"item": "The item must have material kind."})

    def __str__(self):
        return str(self.item)


class BOMLine(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="bom_lines")
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6, help_text="Standard material quantity per batch")
    unit = models.CharField(max_length=6, choices=Unit.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "material"], name="unique_bom_material"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="positive_bom_quantity"),
        ]

    def clean(self):
        if self.material_id:
            convert_quantity(self.quantity, self.unit, self.material.item.base_unit)


class Purchase(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECEIVED = "received", "Received"

    reference = models.CharField(max_length=60, unique=True)
    date = models.DateField()
    supplier_name = models.CharField(max_length=200)
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=6, choices=Unit.choices)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="positive_purchase_quantity"),
            models.CheckConstraint(condition=models.Q(unit_cost__gte=0), name="nonnegative_purchase_cost"),
        ]

    def clean(self):
        if self.material_id:
            convert_quantity(self.quantity, self.unit, self.material.item.base_unit)

    def __str__(self):
        return self.reference


class ProductionRun(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        COMPLETED = "completed", "Completed"

    reference = models.CharField(max_length=60, unique=True)
    date = models.DateField()
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    planned_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    completed_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    unit = models.CharField(max_length=6, choices=Unit.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLANNED)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(planned_quantity__gt=0), name="positive_planned_quantity"),
            models.CheckConstraint(condition=models.Q(completed_quantity__gte=0), name="nonnegative_completed_quantity"),
        ]

    def clean(self):
        if self.product_id:
            convert_quantity(self.planned_quantity, self.unit, self.product.item.base_unit)
        if self.completed_quantity > self.planned_quantity:
            raise ValidationError({"completed_quantity": "Cannot exceed planned quantity."})

    def __str__(self):
        return self.reference


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        SHIPPED = "shipped", "Shipped"

    reference = models.CharField(max_length=60, unique=True)
    date = models.DateField()
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    def __str__(self):
        return self.reference


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    line_number = models.PositiveIntegerField()
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=6, choices=Unit.choices)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "line_number"], name="unique_order_line"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="positive_order_quantity"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="nonnegative_order_price"),
        ]

    def clean(self):
        if self.product_id:
            convert_quantity(self.quantity, self.unit, self.product.item.base_unit)


class Invoice(models.Model):
    number = models.CharField(max_length=60, unique=True)
    date = models.DateField()
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="invoices")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0), name="nonnegative_invoice_amount")]

    @property
    def balance_due(self):
        paid = self.payments.aggregate(total=Coalesce(Sum("amount"), Value(Decimal("0")), output_field=DecimalField(max_digits=14, decimal_places=2)))["total"]
        return self.amount - paid

    def __str__(self):
        return self.number


class Payment(models.Model):
    reference = models.CharField(max_length=60, unique=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=60, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="positive_payment_amount")]

    def __str__(self):
        return self.reference


class StockMovementQuerySet(models.QuerySet):
    def balance_for(self, item, as_of=None):
        movements = self.filter(item=item)
        if as_of is not None:
            movements = movements.filter(date__lte=as_of)
        return movements.aggregate(total=Coalesce(
            Sum("base_quantity"), Value(Decimal("0")),
            output_field=DecimalField(max_digits=24, decimal_places=6),
        ))["total"]


class StockMovement(models.Model):
    class Source(models.TextChoices):
        OPENING = "opening", "Opening balance"
        PURCHASE = "purchase", "Purchase"
        PRODUCTION = "production", "Production run"
        ORDER = "order", "Order"
        ADJUSTMENT = "adjustment", "Adjustment"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    quantity = models.DecimalField(max_digits=18, decimal_places=6, help_text="Signed quantity in the recorded unit")
    unit = models.CharField(max_length=6, choices=Unit.choices)
    base_quantity = models.DecimalField(max_digits=24, decimal_places=6, editable=False)
    date = models.DateField(db_index=True)
    source_type = models.CharField(max_length=12, choices=Source.choices)
    source_reference = models.CharField(max_length=100)
    line_number = models.PositiveIntegerField(default=1)
    note = models.TextField(blank=True)

    objects = StockMovementQuerySet.as_manager()

    class Meta:
        ordering = ["date", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["source_type", "source_reference", "item", "line_number"], name="unique_stock_source_line"),
            models.CheckConstraint(condition=~models.Q(quantity=0), name="nonzero_movement_quantity"),
            models.CheckConstraint(condition=~models.Q(source_reference=""), name="nonempty_stock_source_reference"),
        ]

    def clean(self):
        if not (self.source_reference or "").strip():
            raise ValidationError({"source_reference": "A source document reference is required."})
        if self.quantity == 0:
            raise ValidationError({"quantity": "A movement cannot have zero quantity."})
        if self.item_id:
            carton_size = None
            if self.item.kind == Item.Kind.PRODUCT and hasattr(self.item, "product"):
                carton_size = self.item.product.packets_per_carton
            self.base_quantity = convert_quantity(self.quantity, self.unit, self.item.base_unit, packets_per_carton=carton_size)

    def save(self, *args, **kwargs):
        self.full_clean()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"base_quantity"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source_type}:{self.source_reference} {self.item.sku} {self.quantity} {self.unit}"
