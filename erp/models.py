from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from .units import Unit, convert_quantity


def _protect_posted_document(instance, fields):
    if instance.pk and instance.stock_movements.exists():
        original = type(instance).objects.get(pk=instance.pk)
        if any(getattr(instance, field) != getattr(original, field) for field in fields):
            raise ValidationError("Posted document fields cannot be changed; record a correcting adjustment instead.")


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

    def save(self, *args, **kwargs):
        if self.pk and self.movements.exists():
            original = type(self).objects.get(pk=self.pk)
            if any(getattr(self, field) != getattr(original, field) for field in ("sku", "kind", "base_unit")):
                raise ValidationError("An item with stock history cannot change SKU, kind, or base unit.")
        super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.get(pk=self.pk).item_id != self.item_id:
            raise ValidationError("A product's stock item cannot be reassigned.")
        super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.item_id != self.item_id:
                raise ValidationError("A material's stock item cannot be reassigned.")
            if self.item.movements.exists() and original.standard_cost != self.standard_cost:
                raise ValidationError("Opening material cost cannot change after stock is posted.")
        super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        _protect_posted_document(self, ("reference", "date", "supplier_name", "material_id", "quantity", "unit", "unit_cost", "status"))
        super().save(*args, **kwargs)

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
    labour_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    power_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Gas and power overhead")

    VARIANCE_ALERT_PERCENT = Decimal("3")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(planned_quantity__gt=0), name="positive_planned_quantity"),
            models.CheckConstraint(condition=models.Q(completed_quantity__gte=0), name="nonnegative_completed_quantity"),
            models.CheckConstraint(condition=models.Q(labour_cost__gte=0), name="nonnegative_run_labour_cost"),
            models.CheckConstraint(condition=models.Q(power_cost__gte=0), name="nonnegative_run_power_cost"),
        ]

    def clean(self):
        if self.product_id:
            convert_quantity(self.planned_quantity, self.unit, self.product.item.base_unit, packets_per_carton=self.product.packets_per_carton)

    def save(self, *args, **kwargs):
        _protect_posted_document(self, ("reference", "date", "product_id", "planned_quantity", "completed_quantity", "unit", "status"))
        super().save(*args, **kwargs)

    @property
    def yield_variance_percent(self):
        planned = convert_quantity(self.planned_quantity, self.unit, self.product.item.base_unit, packets_per_carton=self.product.packets_per_carton)
        actual = convert_quantity(self.completed_quantity, self.unit, self.product.item.base_unit, packets_per_carton=self.product.packets_per_carton)
        return (actual - planned) * Decimal("100") / planned

    @property
    def yield_variance_alert(self):
        return abs(self.yield_variance_percent) > self.VARIANCE_ALERT_PERCENT

    @property
    def material_cost(self):
        return self.consumptions.aggregate(total=Coalesce(
            Sum("extended_cost"), Value(Decimal("0")),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ))["total"]

    @property
    def total_cost(self):
        return self.material_cost + self.labour_cost + self.power_cost

    @property
    def cost_per_packet(self):
        good_output = convert_quantity(
            self.completed_quantity, self.unit, self.product.item.base_unit,
            packets_per_carton=self.product.packets_per_carton,
        )
        if good_output <= 0:
            return None
        return (self.total_cost / good_output).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def margin_per_packet(self):
        if self.cost_per_packet is None:
            return None
        return self.product.selling_price - self.cost_per_packet

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

    def save(self, *args, **kwargs):
        _protect_posted_document(self, ("reference", "date", "customer_id", "salesperson_id", "status"))
        super().save(*args, **kwargs)

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
            convert_quantity(self.quantity, self.unit, self.product.item.base_unit, packets_per_carton=self.product.packets_per_carton)

    def save(self, *args, **kwargs):
        if self.order_id and self.order.stock_movements.exists():
            raise ValidationError("Dispatched order lines cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.order.stock_movements.exists():
            raise ValidationError("Dispatched order lines cannot be deleted.")
        return super().delete(*args, **kwargs)


class ProductionConsumption(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="consumptions")
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6, help_text="Actual issued quantity, including wastage")
    unit = models.CharField(max_length=6, choices=Unit.choices)
    wastage_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    standard_quantity_snapshot = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True, editable=False)
    standard_unit_snapshot = models.CharField(max_length=6, choices=Unit.choices, blank=True, default="", editable=False)
    unit_cost_snapshot = models.DecimalField(max_digits=18, decimal_places=6, default=0, editable=False)
    extended_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "material"], name="unique_run_material"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="positive_consumption_quantity"),
            models.CheckConstraint(condition=models.Q(wastage_quantity__gte=0), name="nonnegative_consumption_wastage"),
        ]

    def clean(self):
        if self.material_id:
            convert_quantity(self.quantity, self.unit, self.material.item.base_unit)
        if self.wastage_quantity > self.quantity:
            raise ValidationError({"wastage_quantity": "Wastage cannot exceed issued quantity."})

    def save(self, *args, **kwargs):
        if self.run_id and self.run.stock_movements.exists():
            raise ValidationError("Posted production consumption cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.run.stock_movements.exists():
            raise ValidationError("Posted production consumption cannot be deleted.")
        return super().delete(*args, **kwargs)

    @property
    def variance_percent(self):
        if self.standard_quantity_snapshot is not None:
            standard = convert_quantity(self.standard_quantity_snapshot, self.standard_unit_snapshot, self.material.item.base_unit)
        else:
            bom_line = BOMLine.objects.get(product=self.run.product, material=self.material)
            batches = convert_quantity(
                self.run.planned_quantity, self.run.unit, self.run.product.item.base_unit,
                packets_per_carton=self.run.product.packets_per_carton,
            ) / Decimal(self.run.product.expected_yield_per_batch)
            standard = convert_quantity(bom_line.quantity, bom_line.unit, self.material.item.base_unit) * batches
        actual = convert_quantity(self.quantity, self.unit, self.material.item.base_unit)
        return (actual - standard) * Decimal("100") / standard

    @property
    def variance_alert(self):
        return abs(self.variance_percent) > ProductionRun.VARIANCE_ALERT_PERCENT


class ProductionCostRevision(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.PROTECT, related_name="cost_revisions")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    changed_at = models.DateTimeField(auto_now_add=True)
    old_labour_cost = models.DecimalField(max_digits=14, decimal_places=2)
    new_labour_cost = models.DecimalField(max_digits=14, decimal_places=2)
    old_power_cost = models.DecimalField(max_digits=14, decimal_places=2)
    new_power_cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["-changed_at"]


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


class StockAdjustment(models.Model):
    class Kind(models.TextChoices):
        OPENING = "opening", "Opening balance"
        ADJUSTMENT = "adjustment", "Stock adjustment"

    reference = models.CharField(max_length=100, unique=True)
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    note = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        if self.pk and self.stock_movements.exists():
            original = type(self).objects.get(pk=self.pk)
            if self.reference != original.reference or self.kind != original.kind or self.date != original.date:
                raise ValidationError("A posted adjustment's reference, kind, and date cannot be changed.")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class StockAdjustmentLine(models.Model):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    line_number = models.PositiveIntegerField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=6, choices=Unit.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["adjustment", "line_number"], name="unique_adjustment_line"),
            models.CheckConstraint(condition=~models.Q(quantity=0), name="nonzero_adjustment_line"),
        ]

    def clean(self):
        if self.item_id:
            carton_size = self.item.product.packets_per_carton if self.item.kind == Item.Kind.PRODUCT else None
            convert_quantity(self.quantity, self.unit, self.item.base_unit, packets_per_carton=carton_size)

    def save(self, *args, **kwargs):
        if self.adjustment_id and self.adjustment.stock_movements.exists():
            if not self.pk:
                raise ValidationError("Posted adjustment lines cannot be added.")
            original = type(self).objects.get(pk=self.pk)
            if any(getattr(self, field) != getattr(original, field) for field in ("adjustment_id", "line_number", "item_id", "quantity", "unit")):
                raise ValidationError("Posted adjustment lines cannot be edited.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.adjustment.stock_movements.exists():
            raise ValidationError("Posted adjustment lines cannot be deleted.")
        return super().delete(*args, **kwargs)


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
        INVOICE = "invoice", "Invoice"
        ADJUSTMENT = "adjustment", "Adjustment"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    quantity = models.DecimalField(max_digits=18, decimal_places=6, help_text="Signed quantity in the recorded unit")
    unit = models.CharField(max_length=6, choices=Unit.choices)
    base_quantity = models.DecimalField(max_digits=24, decimal_places=6, editable=False)
    date = models.DateField(db_index=True)
    source_type = models.CharField(max_length=12, choices=Source.choices)
    source_reference = models.CharField(max_length=100)
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.PROTECT, related_name="stock_movements", null=True, blank=True)
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="stock_movements", null=True, blank=True)
    production_run = models.ForeignKey(ProductionRun, on_delete=models.PROTECT, related_name="stock_movements", null=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="stock_movements", null=True, blank=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="stock_movements", null=True, blank=True)
    line_number = models.PositiveIntegerField(default=1)
    note = models.TextField(blank=True)

    objects = StockMovementQuerySet.as_manager()

    class Meta:
        ordering = ["date", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["source_type", "source_reference", "item", "line_number"], name="unique_stock_source_line"),
            models.CheckConstraint(condition=~models.Q(quantity=0), name="nonzero_movement_quantity"),
            models.CheckConstraint(condition=~models.Q(source_reference=""), name="nonempty_stock_source_reference"),
            models.CheckConstraint(
                condition=(
                    models.Q(source_type__in=["opening", "adjustment"], adjustment__isnull=False, purchase__isnull=True, production_run__isnull=True, order__isnull=True, invoice__isnull=True)
                    | models.Q(source_type="purchase", adjustment__isnull=True, purchase__isnull=False, production_run__isnull=True, order__isnull=True, invoice__isnull=True)
                    | models.Q(source_type="production", adjustment__isnull=True, purchase__isnull=True, production_run__isnull=False, order__isnull=True, invoice__isnull=True)
                    | models.Q(source_type="order", adjustment__isnull=True, purchase__isnull=True, production_run__isnull=True, order__isnull=False, invoice__isnull=True)
                    | models.Q(source_type="invoice", adjustment__isnull=True, purchase__isnull=True, production_run__isnull=True, order__isnull=True, invoice__isnull=False)
                ),
                name="stock_movement_has_document",
            ),
        ]

    @property
    def source_document(self):
        for field in ("adjustment", "purchase", "production_run", "order", "invoice"):
            if getattr(self, f"{field}_id") is not None:
                return getattr(self, field)
        return None

    def clean(self):
        if not (self.source_reference or "").strip():
            raise ValidationError({"source_reference": "A source document reference is required."})
        if self.quantity == 0:
            raise ValidationError({"quantity": "A movement cannot have zero quantity."})
        source_fields = {
            self.Source.OPENING: "adjustment",
            self.Source.ADJUSTMENT: "adjustment",
            self.Source.PURCHASE: "purchase",
            self.Source.PRODUCTION: "production_run",
            self.Source.ORDER: "order",
            self.Source.INVOICE: "invoice",
        }
        linked = [field for field in ("adjustment", "purchase", "production_run", "order", "invoice") if getattr(self, f"{field}_id") is not None]
        expected_field = source_fields.get(self.source_type)
        if linked != [expected_field]:
            raise ValidationError("Exactly one matching source document must be linked to each movement.")
        document = getattr(self, expected_field)
        if self.source_type in {self.Source.OPENING, self.Source.ADJUSTMENT} and document.kind != self.source_type:
            raise ValidationError("Adjustment kind must match the movement source type.")
        document_reference = document.number if expected_field == "invoice" else document.reference
        if self.source_reference != document_reference:
            raise ValidationError({"source_reference": "Reference must match the linked document."})
        if self.source_type in {self.Source.OPENING, self.Source.ADJUSTMENT}:
            line = document.lines.filter(line_number=self.line_number).first()
            if line is None or line.item_id != self.item_id or line.quantity != self.quantity or line.unit != self.unit:
                raise ValidationError("Adjustment movement must match its document line.")
        if self.source_type == self.Source.PURCHASE:
            if (
                document.status != Purchase.Status.RECEIVED
                or self.item_id != document.material.item_id
                or self.quantity != document.quantity
                or self.unit != document.unit
            ):
                raise ValidationError("Purchase movements must add the received material.")
        if self.source_type == self.Source.PRODUCTION:
            if document.status != ProductionRun.Status.COMPLETED:
                raise ValidationError("Production movements require a completed run.")
            if self.item_id == document.product.item_id:
                if self.quantity != document.completed_quantity or self.unit != document.unit:
                    raise ValidationError("Finished output must match the run's good output.")
            else:
                consumption = document.consumptions.filter(material__item_id=self.item_id).first()
                if consumption is None or self.quantity != -consumption.quantity or self.unit != consumption.unit:
                    raise ValidationError("Material issue must match actual run consumption.")
        if self.source_type == self.Source.ORDER:
            line = document.lines.filter(line_number=self.line_number).first()
            if document.status != Order.Status.SHIPPED or line is None or self.item_id != line.product.item_id or self.quantity != -line.quantity or self.unit != line.unit:
                raise ValidationError("Order dispatch must match its shipped order line.")
        if self.source_type == self.Source.INVOICE:
            line = document.order.lines.filter(line_number=self.line_number).first()
            if line is None or self.item_id != line.product.item_id or self.quantity != -line.quantity or self.unit != line.unit:
                raise ValidationError("Invoice movement must match its order line.")
        if self.item_id:
            carton_size = None
            if self.item.kind == Item.Kind.PRODUCT and hasattr(self.item, "product"):
                carton_size = self.item.product.packets_per_carton
            self.base_quantity = convert_quantity(self.quantity, self.unit, self.item.base_unit, packets_per_carton=carton_size)

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            immutable = ("item_id", "quantity", "unit", "date", "source_type", "source_reference", "line_number", "adjustment_id", "purchase_id", "production_run_id", "order_id", "invoice_id", "note")
            if any(getattr(self, field) != getattr(original, field) for field in immutable):
                raise ValidationError("Posted stock movements are immutable; record a correcting adjustment.")
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"base_quantity"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source_type}:{self.source_reference} {self.item.sku} {self.quantity} {self.unit}"
