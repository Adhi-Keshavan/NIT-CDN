"""Role-specific sales, review, invoice, dispatch, and collection pages."""

from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import AccessRequest, User
from accounts.views import roles_required

from .billing import invoice_calculation, issue_credit_note, issue_invoice, record_payment
from .forms import CompanyProfileForm, CreditNoteForm, InvoiceIssueForm, OrderLineEntryFormSet, PaymentEntryForm, SalesOrderForm
from .invoice_pdf import credit_note_pdf_bytes, invoice_pdf_bytes
from .ledger import approve_order, dispatch_order, reject_order, stock_shortages_for_order
from .models import CompanyProfile, CreditNote, Customer, Invoice, Order, OrderLine, Product, StockMovement
from .units import convert_quantity


sales_access = roles_required(User.Role.SALESMAN, User.Role.ADMIN)
admin_access = roles_required(User.Role.ADMIN)


def _visible_order(request, pk):
    queryset = Order.objects.select_related("customer", "salesperson")
    if request.user.role != User.Role.ADMIN:
        queryset = queryset.filter(salesperson=request.user)
    return get_object_or_404(queryset, pk=pk)


def _save_lines(order, formset):
    for number, form in enumerate((form for form in formset if form.cleaned_data and not form.cleaned_data.get("DELETE")), start=1):
        product = Product.objects.select_related("item").get(pk=form.cleaned_data["product"].pk)
        line = OrderLine(
            order=order, line_number=number, product=product,
            quantity=form.cleaned_data["quantity"],
            unit=product.item.base_unit, unit_price=product.selling_price,
        )
        line.full_clean()
        line.save()


@sales_access
def sales_home(request):
    orders = Order.objects.select_related("customer").order_by("-date", "-pk")
    if request.user.role != User.Role.ADMIN:
        orders = orders.filter(salesperson=request.user)
    return render(request, "erp/sales_home.html", {"orders": orders[:50]})


@sales_access
def sales_order_new(request):
    form = SalesOrderForm(request.POST or None)
    formset = OrderLineEntryFormSet(request.POST or None, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                day = timezone.localdate()
                order = Order.objects.create(
                    reference=f"ORD-{day:%Y%m%d}-{uuid4().hex[:10].upper()}",
                    date=day, customer=form.cleaned_data["customer"], salesperson=request.user,
                )
                _save_lines(order, formset)
                if not order.lines.exists():
                    raise ValidationError("Add at least one product.")
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Order {order.reference} submitted for admin review.")
            return redirect("sales_order_detail", pk=order.pk)
    return render(request, "erp/order_form.html", {"form": form, "formset": formset, "editing": False})


@sales_access
def sales_order_detail(request, pk):
    order = _visible_order(request, pk)
    invoice = order.invoices.first()
    return render(request, "erp/sales_order_detail.html", {
        "order": order, "lines": order.lines.select_related("product__item").order_by("line_number"),
        "invoice": invoice,
    })


@admin_access
def management_home(request):
    orders = Order.objects.select_related("customer", "salesperson").order_by("-date", "-pk")[:50]
    return render(request, "erp/management_home.html", {
        "orders": orders,
        "company": CompanyProfile.objects.filter(pk=1).first(),
        "pending_access_count": AccessRequest.objects.filter(status=AccessRequest.Status.PENDING).count(),
        "customers": [(customer, sum((invoice.balance_due for invoice in Invoice.objects.filter(order__customer=customer)), Decimal("0.00"))) for customer in Customer.objects.order_by("name")],
    })


def _availability(order):
    shortages = stock_shortages_for_order(order)
    rows = []
    for line in order.lines.select_related("product__item").order_by("line_number"):
        item = line.product.item
        available = StockMovement.objects.balance_for(item)
        required = convert_quantity(line.quantity, line.unit, item.base_unit, packets_per_carton=line.product.packets_per_carton)
        rows.append({"line": line, "available": available, "required": required, "shortage": shortages.get(item.sku, Decimal("0"))})
    return rows, shortages


@admin_access
def order_review(request, pk):
    order = get_object_or_404(Order.objects.select_related("customer", "salesperson"), pk=pk)
    error = None
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "approve":
                approve_order(order, reviewed_by=request.user)
                messages.success(request, "Order approved.")
            elif action == "reject":
                reject_order(order, reason=request.POST.get("reason", ""), reviewed_by=request.user)
                messages.success(request, "Order rejected.")
            elif action == "dispatch":
                dispatch_order(order)
                messages.success(request, "Order dispatched; stock ledger updated.")
            else:
                raise ValidationError("Unknown order action.")
        except ValidationError as exc:
            error = str(exc)
        else:
            return redirect("order_review", pk=order.pk)
    rows, shortages = _availability(order)
    return render(request, "erp/order_review.html", {
        "order": order, "rows": rows, "shortages": shortages,
        "invoice": order.invoices.first(), "error": error,
    })


@admin_access
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.status != Order.Status.DRAFT:
        raise PermissionDenied("Only draft orders can be edited.")
    initial = [{"product": line.product_id, "quantity": line.quantity} for line in order.lines.order_by("line_number")]
    form = SalesOrderForm(request.POST or None, initial={"customer": order.customer_id})
    formset = OrderLineEntryFormSet(request.POST or None, prefix="lines", initial=initial)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                if locked.status != Order.Status.DRAFT:
                    raise ValidationError("Order was already reviewed.")
                locked.customer = form.cleaned_data["customer"]
                locked.save(update_fields=["customer"])
                locked.lines.all().delete()
                _save_lines(locked, formset)
                if not locked.lines.exists():
                    raise ValidationError("Add at least one product.")
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Draft order updated using current product prices.")
            return redirect("order_review", pk=order.pk)
    return render(request, "erp/order_form.html", {"form": form, "formset": formset, "editing": True, "order": order})


@admin_access
def company_profile(request):
    company = CompanyProfile.objects.filter(pk=1).first()
    form = CompanyProfileForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        company = form.save(commit=False)
        company.pk = 1
        company.save()
        messages.success(request, "Company invoice details saved.")
        return redirect("management")
    return render(request, "erp/company_form.html", {"form": form})


@admin_access
def invoice_issue(request, pk):
    order = get_object_or_404(Order.objects.select_related("customer"), pk=pk)
    company = CompanyProfile.objects.filter(pk=1).first()
    if order.status != Order.Status.CONFIRMED or order.invoices.exists():
        return redirect("order_review", pk=order.pk)
    form = InvoiceIssueForm(request.POST or None, initial={
        "place_of_supply_state": order.customer.state,
        "place_of_supply_code": order.customer.state_code,
    })
    error = None
    preview = None
    if company:
        try:
            preview = invoice_calculation(order, company=company, place_of_supply_code=(request.POST.get("place_of_supply_code") if request.method == "POST" else order.customer.state_code))
        except ValidationError as exc:
            error = str(exc)
    else:
        error = "Set the company GST details before issuing invoices."
    if request.method == "POST" and form.is_valid() and company and not error:
        try:
            invoice = issue_invoice(
                order, company=company,
                issue_date=form.cleaned_data["issue_date"], due_date=form.cleaned_data["due_date"],
                place_of_supply_state=form.cleaned_data["place_of_supply_state"],
                place_of_supply_code=form.cleaned_data["place_of_supply_code"],
                issued_by=request.user,
            )
        except (ValidationError, IntegrityError) as exc:
            error = str(exc)
        else:
            messages.success(request, f"Invoice {invoice.number} issued.")
            return redirect("invoice_detail", pk=invoice.pk)
    return render(request, "erp/invoice_issue.html", {"order": order, "company": company, "form": form, "error": error, "preview": preview})


def _visible_invoice(request, pk):
    queryset = Invoice.objects.select_related("order__customer", "order__salesperson")
    if request.user.role != User.Role.ADMIN:
        queryset = queryset.filter(order__salesperson=request.user)
    return get_object_or_404(queryset, pk=pk)


@sales_access
def invoice_detail(request, pk):
    invoice = _visible_invoice(request, pk)
    return render(request, "erp/invoice_detail.html", {
        "invoice": invoice, "lines": invoice.lines.all(), "payments": invoice.payments.order_by("date", "pk"),
        "credit_note": getattr(invoice, "credit_note", None), "audit_events": invoice.audit_events.select_related("actor"),
    })


@sales_access
def invoice_pdf(request, pk):
    invoice = _visible_invoice(request, pk)
    response = HttpResponse(invoice_pdf_bytes(invoice), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.number.replace("/", "-")}.pdf"'
    return response


@admin_access
def payment_new(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if hasattr(invoice, "credit_note"):
        raise PermissionDenied("Credited invoices cannot receive payments.")
    form = PaymentEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            record_payment(invoice, **form.cleaned_data, recorded_by=request.user)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment recorded; outstanding balance updated.")
            return redirect("invoice_detail", pk=invoice.pk)
    return render(request, "erp/payment_form.html", {"invoice": invoice, "form": form})


@admin_access
def credit_note_new(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("order__customer"), pk=pk)
    if hasattr(invoice, "credit_note"):
        return redirect("invoice_detail", pk=pk)
    form = CreditNoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            note = issue_credit_note(invoice, **form.cleaned_data, issued_by=request.user)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Credit note {note.number} issued. The original invoice remains in the audit trail.")
            return redirect("invoice_detail", pk=pk)
    return render(request, "erp/credit_note_form.html", {"invoice": invoice, "form": form})


@sales_access
def credit_note_detail(request, pk):
    note = get_object_or_404(CreditNote.objects.select_related("invoice"), pk=pk)
    _visible_invoice(request, note.invoice_id)
    return render(request, "erp/credit_note_detail.html", {"note": note, "invoice": note.invoice, "lines": note.invoice.lines.all()})


@sales_access
def credit_note_pdf(request, pk):
    note = get_object_or_404(CreditNote.objects.select_related("invoice"), pk=pk)
    _visible_invoice(request, note.invoice_id)
    response = HttpResponse(credit_note_pdf_bytes(note), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{note.number.replace("/", "-")}.pdf"'
    return response
