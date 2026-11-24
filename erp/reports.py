"""Read-only, date-filtered registers built from posted documents and ledger entries."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from .models import CreditNote, Customer, Invoice, InvoiceLine, Item, Payment, ProductionRun, Purchase, StockMovement


REPORTS = {
    "sales": ("Sales register", ("Date", "Document", "Customer", "GSTIN", "Place of supply", "Taxable", "CGST", "SGST", "IGST", "Total")),
    "gst": ("GST summary", ("GST rate", "Taxable", "CGST", "SGST", "IGST", "Total tax")),
    "purchases": ("Purchase register", ("Date", "Reference", "Supplier", "Material", "Quantity", "Unit", "Rate", "Value")),
    "stock": ("Stock summary", ("SKU", "Item", "Unit", "Opening", "Receipts", "Issues", "Closing")),
    "ledger": ("Stock ledger", ("Date", "SKU", "Item", "Source", "Reference", "Change", "Unit", "Balance")),
    "production": ("Production variance and cost", ("Date", "Run", "Product", "Expected", "Good", "Yield variance %", "Material variances", "Alerts", "Material", "Labour", "Gas/power", "Total", "Cost/packet")),
    "collections": ("Collections and outstanding", ("Customer", "Invoiced", "Collected", "Outstanding", "Not due", "1-30 days", "31-60 days", "61+ days")),
}


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or Decimal("0")


def _sales(start, end):
    rows = []
    for invoice in Invoice.objects.filter(date__range=(start, end)).select_related("order__customer").order_by("date", "number"):
        rows.append((invoice.date, invoice.number, invoice.customer_name, invoice.customer_gstin, invoice.place_of_supply_state,
                     invoice.taxable_value, invoice.cgst_amount, invoice.sgst_amount, invoice.igst_amount, invoice.amount))
    for note in CreditNote.objects.filter(date__range=(start, end)).select_related("invoice").order_by("date", "number"):
        rows.append((note.date, f"{note.number} / {note.invoice.number}", note.invoice.customer_name,
                     note.invoice.customer_gstin, note.invoice.place_of_supply_state,
                     -note.taxable_value, -note.cgst_amount, -note.sgst_amount, -note.igst_amount, -note.amount))
    return sorted(rows, key=lambda row: (row[0], row[1]))


def _gst(start, end):
    totals = defaultdict(lambda: [Decimal("0.00") for _ in range(5)])
    fields = ("taxable_value", "cgst_amount", "sgst_amount", "igst_amount")
    lines = InvoiceLine.objects.filter(invoice__date__range=(start, end))
    for line in lines:
        bucket = totals[line.gst_rate]
        for index, field in enumerate(fields):
            bucket[index] += getattr(line, field)
    credits = InvoiceLine.objects.filter(invoice__credit_note__date__range=(start, end))
    for line in credits:
        bucket = totals[line.gst_rate]
        for index, field in enumerate(fields):
            bucket[index] -= getattr(line, field)
    return [(rate, *values[:4], sum(values[1:4], Decimal("0.00"))) for rate, values in sorted(totals.items())]


def _purchases(start, end):
    return [
        (purchase.date, purchase.reference, purchase.supplier_name, purchase.material.item.name,
         purchase.quantity, purchase.unit, purchase.unit_cost, purchase.quantity * purchase.unit_cost)
        for purchase in Purchase.objects.filter(date__range=(start, end), status=Purchase.Status.RECEIVED)
        .select_related("material__item").order_by("date", "reference")
    ]


def _stock(start, end, item=None):
    items = Item.objects.order_by("sku")
    if item:
        items = items.filter(pk=item.pk)
    rows = []
    for stock_item in items:
        opening = StockMovement.objects.balance_for(stock_item, as_of=start - timedelta(days=1))
        movements = StockMovement.objects.filter(item=stock_item, date__range=(start, end))
        receipts = _sum(movements.filter(base_quantity__gt=0), "base_quantity")
        issues = -_sum(movements.filter(base_quantity__lt=0), "base_quantity")
        rows.append((stock_item.sku, stock_item.name, stock_item.base_unit, opening, receipts, issues, opening + receipts - issues))
    return rows


def _ledger(start, end, item=None):
    movements = StockMovement.objects.filter(date__range=(start, end)).select_related("item").order_by("item__sku", "date", "pk")
    if item:
        movements = movements.filter(item=item)
    rows = []
    balances = {}
    for movement in movements:
        if movement.item_id not in balances:
            balances[movement.item_id] = StockMovement.objects.balance_for(movement.item, as_of=start - timedelta(days=1))
            rows.append((start, movement.item.sku, movement.item.name, "Opening", "", Decimal("0"), movement.item.base_unit, balances[movement.item_id]))
        balances[movement.item_id] += movement.base_quantity
        rows.append((movement.date, movement.item.sku, movement.item.name, movement.get_source_type_display(), movement.source_reference, movement.base_quantity, movement.item.base_unit, balances[movement.item_id]))
    if item and item.pk not in balances:
        opening = StockMovement.objects.balance_for(item, as_of=start - timedelta(days=1))
        rows.append((start, item.sku, item.name, "Opening", "", Decimal("0"), item.base_unit, opening))
    return rows


def _production(start, end):
    rows = []
    for run in ProductionRun.objects.filter(date__range=(start, end), status=ProductionRun.Status.COMPLETED).select_related("product__item").prefetch_related("consumptions").order_by("date", "reference"):
        entries = list(run.consumptions.select_related("material__item"))
        material_alerts = [entry.material.item.sku for entry in entries if entry.variance_alert]
        variances = ", ".join(f"{entry.material.item.sku} {entry.variance_percent:+.2f}%" for entry in entries)
        alerts = (["Yield"] if run.yield_variance_alert else []) + material_alerts
        rows.append((run.date, run.reference, run.product.item.name, run.planned_quantity, run.completed_quantity,
                     run.yield_variance_percent.quantize(Decimal("0.01")), variances, ", ".join(alerts) or "None",
                     run.material_cost, run.labour_cost, run.power_cost, run.total_cost, run.cost_per_packet))
    return rows


def _collections(start, end):
    rows = []
    for customer in Customer.objects.order_by("name"):
        invoices = Invoice.objects.filter(order__customer=customer, date__lte=end).select_related("credit_note")
        invoiced = _sum(invoices.filter(date__gte=start), "amount")
        notes = CreditNote.objects.filter(invoice__order__customer=customer, date__range=(start, end))
        invoiced -= _sum(notes, "amount")
        payments = Payment.objects.filter(invoice__order__customer=customer, date__range=(start, end))
        collected = _sum(payments, "amount")
        buckets = [Decimal("0.00") for _ in range(4)]
        for invoice in invoices:
            paid = _sum(invoice.payments.filter(date__lte=end), "amount")
            credited = invoice.credit_note.amount if hasattr(invoice, "credit_note") and invoice.credit_note.date <= end else Decimal("0.00")
            remaining = invoice.amount - paid - credited
            if remaining <= 0:
                continue
            age = (end - (invoice.due_date or invoice.date)).days
            buckets[0 if age <= 0 else 1 if age <= 30 else 2 if age <= 60 else 3] += remaining
        rows.append((customer.name, invoiced, collected, sum(buckets, Decimal("0.00")), *buckets))
    return rows


BUILDERS = {
    "sales": _sales, "gst": _gst, "purchases": _purchases, "stock": _stock,
    "ledger": _ledger, "production": _production, "collections": _collections,
}


def build_report(kind, start, end, *, item=None):
    title, columns = REPORTS[kind]
    if kind in {"stock", "ledger"}:
        rows = BUILDERS[kind](start, end, item)
    else:
        rows = BUILDERS[kind](start, end)
    return title, columns, rows
