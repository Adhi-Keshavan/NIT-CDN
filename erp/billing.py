"""Immutable GST invoice snapshots and payment posting."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import AuditEvent, CompanyProfile, CreditNote, CreditNoteSequence, Invoice, InvoiceLine, InvoiceSequence, Order, Payment


PAISA = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(PAISA, rounding=ROUND_HALF_UP)


def financial_year(day):
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _state_code(value, label):
    if len(value or "") != 2 or not value.isdigit():
        raise ValidationError(f"{label} must be a two-digit state code.")


def _gstin(value, state_code, label):
    if len(value or "") != 15 or value[:2] != state_code or not value[2:].isalnum():
        raise ValidationError(f"{label} must be a 15-character GSTIN beginning with its state code.")


def invoice_calculation(order, *, company, place_of_supply_code):
    """Calculate each line independently, rounding each tax component to paise."""
    _state_code(company.state_code, "Supplier state")
    _gstin(company.gstin, company.state_code, "Supplier GSTIN")
    _state_code(place_of_supply_code, "Place of supply")
    _state_code(order.customer.state_code, "Customer state")
    if order.customer.gstin:
        _gstin(order.customer.gstin, order.customer.state_code, "Customer GSTIN")
    if not company.name.strip() or not company.address.strip() or not company.state.strip():
        raise ValidationError("Complete the company profile before invoicing.")
    lines = []
    for order_line in order.lines.select_related("product__item").order_by("line_number"):
        order_line.full_clean()
        product = order_line.product
        if not product.hsn_code.strip():
            raise ValidationError(f"Set the HSN code for {product.item.sku} before invoicing.")
        taxable = money(order_line.quantity * order_line.unit_price)
        rate = product.gst_rate
        if company.state_code == place_of_supply_code:
            cgst = money(taxable * rate / Decimal("200"))
            sgst = money(taxable * rate / Decimal("200"))
            igst = Decimal("0.00")
        else:
            cgst = sgst = Decimal("0.00")
            igst = money(taxable * rate / Decimal("100"))
        lines.append({
            "line_number": order_line.line_number,
            "description": product.item.name,
            "hsn_code": product.hsn_code,
            "quantity": order_line.quantity,
            "unit": order_line.unit,
            "unit_price": order_line.unit_price,
            "taxable_value": taxable,
            "gst_rate": rate,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
        })
    if not lines:
        raise ValidationError("An invoice needs at least one order line.")
    totals = {
        key: sum((line[key] for line in lines), Decimal("0.00"))
        for key in ("taxable_value", "cgst_amount", "sgst_amount", "igst_amount")
    }
    totals["amount"] = sum(totals.values(), Decimal("0.00"))
    return lines, totals


@transaction.atomic
def issue_invoice(order, *, company, issue_date, place_of_supply_state, place_of_supply_code, due_date=None, issued_by=None):
    order = Order.objects.select_for_update().select_related("customer").get(pk=order.pk)
    company = CompanyProfile.objects.get(pk=company.pk)
    if order.status != Order.Status.CONFIRMED:
        raise ValidationError("Approve the order before issuing an invoice.")
    if order.invoices.exists():
        raise ValidationError("This order already has an issued invoice.")
    if issue_date < order.date:
        raise ValidationError("Invoice date cannot precede the order date.")
    if due_date and due_date < issue_date:
        raise ValidationError("Due date cannot precede the invoice date.")
    if not place_of_supply_state.strip():
        raise ValidationError("Place of supply state is required.")
    lines, totals = invoice_calculation(order, company=company, place_of_supply_code=place_of_supply_code)
    year = financial_year(issue_date)
    sequence, created = InvoiceSequence.objects.select_for_update().get_or_create(financial_year=year)
    if created:
        prefix = f"INV/{year}/"
        existing = [int(number[len(prefix):]) for number in Invoice.objects.filter(number__startswith=prefix).values_list("number", flat=True) if number[len(prefix):].isdigit()]
        sequence.next_number = max(existing, default=0) + 1
    number = f"INV/{year}/{sequence.next_number:04d}"
    sequence.next_number += 1
    sequence.save(update_fields=["next_number"])
    customer = order.customer
    invoice = Invoice(
        number=number, date=issue_date, order=order, due_date=due_date,
        supplier_name=company.name, supplier_address=company.address,
        supplier_gstin=company.gstin, supplier_state=company.state,
        supplier_state_code=company.state_code,
        customer_name=customer.name, customer_address=customer.address,
        customer_gstin=customer.gstin, customer_state=customer.state,
        customer_state_code=customer.state_code,
        place_of_supply_state=place_of_supply_state,
        place_of_supply_code=place_of_supply_code,
        **totals,
    )
    invoice.full_clean()
    invoice.save()
    for line in lines:
        snapshot = InvoiceLine(invoice=invoice, **line)
        snapshot._allow_snapshot_create = True
        snapshot.save()
    AuditEvent.objects.create(invoice=invoice, actor=issued_by, action="invoice_issued", details={"number": number, "amount": str(invoice.amount)})
    return invoice


@transaction.atomic
def issue_credit_note(invoice, *, date, reason, issued_by=None):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if hasattr(invoice, "credit_note"):
        raise ValidationError("This invoice already has a credit note.")
    if invoice.payments.exists():
        raise ValidationError("Recorded payments must be refunded or reconciled before a full credit note can be issued.")
    if date < invoice.date:
        raise ValidationError("Credit note date cannot precede the invoice date.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("A cancellation reason is required.")
    year = financial_year(date)
    sequence, _ = CreditNoteSequence.objects.select_for_update().get_or_create(financial_year=year)
    number = f"CN/{year}/{sequence.next_number:04d}"
    sequence.next_number += 1
    sequence.save(update_fields=["next_number"])
    note = CreditNote.objects.create(
        invoice=invoice, number=number, date=date, reason=reason, issued_by=issued_by,
        taxable_value=invoice.taxable_value, cgst_amount=invoice.cgst_amount,
        sgst_amount=invoice.sgst_amount, igst_amount=invoice.igst_amount, amount=invoice.amount,
    )
    AuditEvent.objects.create(invoice=invoice, actor=issued_by, action="credit_note_issued", details={"number": number, "reason": reason, "amount": str(note.amount)})
    return note


@transaction.atomic
def record_payment(invoice, *, reference, date, amount, method="", recorded_by=None):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if hasattr(invoice, "credit_note"):
        raise ValidationError("A credited invoice cannot receive payments.")
    payment = Payment(invoice=invoice, reference=reference, date=date, amount=amount, method=method)
    payment.save()
    AuditEvent.objects.create(invoice=invoice, actor=recorded_by, action="payment_recorded", details={"reference": reference, "amount": str(amount)})
    return payment
