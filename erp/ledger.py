"""Atomic, idempotent stock posting from business documents."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .costing import material_issue_cost
from .models import AuditEvent, BOMLine, Item, Order, ProductionCostRevision, ProductionRun, Purchase, StockAdjustment, StockMovement
from .units import convert_quantity


def _base_quantity(item, quantity, unit):
    carton_size = item.product.packets_per_carton if item.kind == Item.Kind.PRODUCT else None
    return convert_quantity(quantity, unit, item.base_unit, packets_per_carton=carton_size)


def _record(*, document, source_type, source_field, item, quantity, unit, line_number=1):
    reference = document.reference
    key = {"source_type": source_type, "source_reference": reference, "item": item, "line_number": line_number}
    existing = StockMovement.objects.select_for_update().filter(**key).first()
    if existing:
        if (
            getattr(existing, f"{source_field}_id") != document.pk
            or existing.quantity != quantity
            or existing.unit != unit
            or existing.date != document.date
        ):
            raise ValidationError(f"Posted stock movement differs from {source_type}:{reference}.")
        return existing
    movement = StockMovement(
        **key, **{source_field: document}, quantity=quantity, unit=unit, date=document.date
    )
    movement.save()
    return movement


def _lock_items(item_ids):
    list(Item.objects.select_for_update().filter(pk__in=item_ids).order_by("pk"))


def _reject_backdated(item_ids, document_date):
    if StockMovement.objects.filter(item_id__in=item_ids, date__gt=document_date).exists():
        raise ValidationError("Post documents in date order to preserve weighted average costing.")


@transaction.atomic
def post_adjustment(adjustment):
    """Post an opening or correcting adjustment from its saved lines."""
    adjustment = StockAdjustment.objects.select_for_update().get(pk=adjustment.pk)
    lines = list(adjustment.lines.select_related("item").order_by("line_number"))
    if not lines:
        raise ValidationError("Adjustment needs at least one line.")
    for line in lines:
        line.full_clean()
    _lock_items([line.item_id for line in lines])
    posted_count = adjustment.stock_movements.count()
    if posted_count and posted_count != len(lines):
        raise ValidationError("Adjustment has an incomplete stock posting.")
    if not posted_count:
        _reject_backdated([line.item_id for line in lines], adjustment.date)
    if not posted_count and adjustment.kind == StockAdjustment.Kind.ADJUSTMENT:
        issues = defaultdict(Decimal)
        for line in lines:
            base = _base_quantity(line.item, line.quantity, line.unit)
            if base < 0:
                issues[line.item_id] += -base
        for item_id, needed in issues.items():
            item = next(line.item for line in lines if line.item_id == item_id)
            available = StockMovement.objects.balance_for(item)
            if available < needed:
                raise ValidationError(f"Insufficient {item.sku}: short {needed - available} {item.base_unit}.")
    return [
        _record(
            document=adjustment, source_type=adjustment.kind,
            source_field="adjustment", item=line.item,
            quantity=line.quantity, unit=line.unit, line_number=line.line_number,
        )
        for line in lines
    ]


@transaction.atomic
def receive_purchase(purchase):
    """Receive a purchase once, adding its material quantity to the ledger."""
    purchase = Purchase.objects.select_for_update().select_related("material__item").get(pk=purchase.pk)
    purchase.full_clean()
    if purchase.status not in {Purchase.Status.DRAFT, Purchase.Status.RECEIVED}:
        raise ValidationError("Purchase has an unsupported status.")
    _lock_items([purchase.material.item_id])
    if not purchase.stock_movements.exists():
        _reject_backdated([purchase.material.item_id], purchase.date)
    if purchase.status == Purchase.Status.DRAFT:
        purchase.status = Purchase.Status.RECEIVED
        purchase.save(update_fields=["status"])
    return _record(
        document=purchase, source_type=StockMovement.Source.PURCHASE,
        source_field="purchase", item=purchase.material.item,
        quantity=purchase.quantity, unit=purchase.unit,
    )


@transaction.atomic
def complete_production_run(run, *, posted_by=None):
    """Post actual material issues and finished output for a saved run."""
    run = ProductionRun.objects.select_for_update().select_related("product__item").get(pk=run.pk)
    run.full_clean()
    if run.completed_quantity <= 0:
        raise ValidationError("Completed quantity must be positive before posting.")
    consumptions = list(run.consumptions.select_related("material__item").order_by("material__item_id"))
    standards = {line.material_id: line for line in BOMLine.objects.filter(product=run.product)}
    standard_ids = set(standards)
    actual_ids = {entry.material_id for entry in consumptions}
    if not standard_ids or actual_ids != standard_ids:
        raise ValidationError("Actual consumption must include every BOM material exactly once.")
    for entry in consumptions:
        entry.full_clean()
    item_ids = [run.product.item_id] + [entry.material.item_id for entry in consumptions]
    _lock_items(item_ids)
    posted_count = run.stock_movements.count()
    if posted_count and posted_count != len(consumptions) + 1:
        raise ValidationError("Production run has an incomplete stock posting.")
    if not posted_count:
        _reject_backdated(item_ids, run.date)
        batches = _base_quantity(run.product.item, run.planned_quantity, run.unit) / Decimal(run.product.expected_yield_per_batch)
        for entry in consumptions:
            needed = _base_quantity(entry.material.item, entry.quantity, entry.unit)
            available = StockMovement.objects.balance_for(entry.material.item)
            if available < needed:
                raise ValidationError(f"Insufficient {entry.material.item.sku}: short {needed - available} {entry.material.item.base_unit}.")
        for entry in consumptions:
            needed = _base_quantity(entry.material.item, entry.quantity, entry.unit)
            rate, extended = material_issue_cost(entry.material, needed, as_of=run.date)
            standard = standards[entry.material_id]
            entry.standard_quantity_snapshot = standard.quantity * batches
            entry.standard_unit_snapshot = standard.unit
            entry.unit_cost_snapshot = rate
            entry.extended_cost = extended
            entry.save(update_fields=["standard_quantity_snapshot", "standard_unit_snapshot", "unit_cost_snapshot", "extended_cost"])
    if run.status == ProductionRun.Status.PLANNED:
        run.status = ProductionRun.Status.COMPLETED
        run.save(update_fields=["status"])
        AuditEvent.objects.create(run=run, actor=posted_by, action="run_posted", details={"reference": run.reference, "good_output": str(run.completed_quantity), "unit": run.unit})
    for entry in consumptions:
        _record(
            document=run, source_type=StockMovement.Source.PRODUCTION,
            source_field="production_run", item=entry.material.item,
            quantity=-entry.quantity, unit=entry.unit,
        )
    return _record(
        document=run, source_type=StockMovement.Source.PRODUCTION,
        source_field="production_run", item=run.product.item,
        quantity=run.completed_quantity, unit=run.unit,
    )


@transaction.atomic
def update_run_overheads(run, *, labour_cost, power_cost, changed_by):
    """Update post-run overheads and keep a user/time stamped revision."""
    run = ProductionRun.objects.select_for_update().get(pk=run.pk)
    if run.status != ProductionRun.Status.COMPLETED:
        raise ValidationError("Only completed runs can receive final overheads.")
    labour_cost = Decimal(str(labour_cost))
    power_cost = Decimal(str(power_cost))
    if labour_cost < 0 or power_cost < 0:
        raise ValidationError("Overhead costs cannot be negative.")
    if (run.labour_cost, run.power_cost) == (labour_cost, power_cost):
        return run
    old_labour, old_power = run.labour_cost, run.power_cost
    run.labour_cost = labour_cost
    run.power_cost = power_cost
    run.full_clean()
    run._allow_overhead_change = True
    run.save(update_fields=["labour_cost", "power_cost"])
    ProductionCostRevision.objects.create(
        run=run, changed_by=changed_by,
        old_labour_cost=old_labour, new_labour_cost=labour_cost,
        old_power_cost=old_power, new_power_cost=power_cost,
    )
    AuditEvent.objects.create(run=run, actor=changed_by, action="overheads_changed", details={
        "old_labour": str(old_labour), "new_labour": str(labour_cost),
        "old_power": str(old_power), "new_power": str(power_cost),
    })
    return run


def stock_shortages_for_order(order):
    """Return positive shortages in each product's base unit."""
    required = defaultdict(Decimal)
    items = {}
    for line in order.lines.select_related("product__item"):
        item = line.product.item
        line.full_clean()
        required[item.pk] += _base_quantity(item, line.quantity, line.unit)
        items[item.pk] = item
    shortages = {}
    for item_id, quantity in required.items():
        available = StockMovement.objects.balance_for(items[item_id])
        if quantity > available:
            shortages[items[item_id].sku] = quantity - available
    return shortages


@transaction.atomic
def approve_order(order, *, reviewed_by=None):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.DRAFT:
        raise ValidationError("Only draft orders can be approved.")
    item_ids = list(order.lines.values_list("product__item_id", flat=True))
    if not item_ids:
        raise ValidationError("Order needs at least one line.")
    _lock_items(item_ids)
    shortages = stock_shortages_for_order(order)
    if shortages:
        raise ValidationError(f"Insufficient finished stock: {shortages}")
    order.status = Order.Status.CONFIRMED
    order.reviewed_by = reviewed_by
    order.reviewed_at = timezone.now()
    order.rejection_reason = ""
    order.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason"])
    return order


@transaction.atomic
def reject_order(order, *, reason, reviewed_by):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.DRAFT:
        raise ValidationError("Only draft orders can be rejected.")
    if not reason.strip():
        raise ValidationError("A rejection reason is required.")
    order.status = Order.Status.REJECTED
    order.rejection_reason = reason.strip()
    order.reviewed_by = reviewed_by
    order.reviewed_at = timezone.now()
    order.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at"])
    return order


@transaction.atomic
def dispatch_order(order):
    """Post finished-goods issues for a confirmed order once."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in {Order.Status.CONFIRMED, Order.Status.SHIPPED}:
        raise ValidationError("Order must be approved before dispatch.")
    if not order.invoices.exists():
        raise ValidationError("Issue an invoice before dispatch.")
    if order.invoices.filter(credit_note__isnull=False).exists():
        raise ValidationError("A credited invoice cannot be dispatched.")
    lines = list(order.lines.select_related("product__item").order_by("line_number"))
    if not lines:
        raise ValidationError("Order needs at least one line.")
    for line in lines:
        line.full_clean()
    _lock_items([line.product.item_id for line in lines])
    posted_count = order.stock_movements.count()
    if posted_count and posted_count != len(lines):
        raise ValidationError("Order has an incomplete stock posting.")
    if not posted_count:
        shortages = stock_shortages_for_order(order)
        if shortages:
            raise ValidationError(f"Insufficient finished stock: {shortages}")
    if order.status == Order.Status.CONFIRMED:
        order.status = Order.Status.SHIPPED
        order.save(update_fields=["status"])
    return [
        _record(
            document=order, source_type=StockMovement.Source.ORDER,
            source_field="order", item=line.product.item,
            quantity=-line.quantity, unit=line.unit, line_number=line.line_number,
        )
        for line in lines
    ]
