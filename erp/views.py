from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from accounts.views import roles_required

from .costing import weighted_average_material_cost
from .forms import OverheadForm, PurchaseReceiptForm, RunEntryForm, RunPlanForm
from .ledger import complete_production_run, receive_purchase, update_run_overheads
from .models import Item, ProductionConsumption, ProductionRun, Purchase, StockMovement
from .units import convert_quantity


supervisor_access = roles_required(User.Role.PRODUCTION, User.Role.ADMIN)


@supervisor_access
def production_home(request):
    return render(request, "erp/production_home.html", {
        "recent_runs": ProductionRun.objects.select_related("product__item").order_by("-date", "-pk")[:5],
        "recent_purchases": Purchase.objects.select_related("material__item").order_by("-date", "-pk")[:5],
    })


@supervisor_access
def purchase_list(request):
    purchases = Purchase.objects.select_related("material__item").order_by("-date", "-pk")[:50]
    return render(request, "erp/purchase_list.html", {"purchases": purchases})


@supervisor_access
def purchase_new(request):
    form = PurchaseReceiptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                purchase = form.save()
                receive_purchase(purchase)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Purchase {purchase.reference} received and stock updated.")
            return redirect("purchase_detail", pk=purchase.pk)
    return render(request, "erp/purchase_form.html", {"form": form})


@supervisor_access
def purchase_detail(request, pk):
    purchase = get_object_or_404(Purchase.objects.select_related("material__item"), pk=pk)
    return render(request, "erp/purchase_detail.html", {
        "purchase": purchase,
        "movement": purchase.stock_movements.first(),
        "balance": StockMovement.objects.balance_for(purchase.material.item),
        "weighted_rate": weighted_average_material_cost(purchase.material),
    })


@supervisor_access
def run_list(request):
    runs = ProductionRun.objects.select_related("product__item").order_by("-date", "-pk")[:50]
    return render(request, "erp/run_list.html", {"runs": runs})


@supervisor_access
def run_new(request):
    plan_data = request.POST if request.method == "POST" else request.GET
    plan_form = RunPlanForm(plan_data or None)
    entry_form = None
    preview = []
    product = None
    batches = None
    expected_output = None
    if plan_data and plan_form.is_valid():
        product = plan_form.cleaned_data["product"]
        batches = plan_form.cleaned_data["batches"]
        expected_output = product.expected_yield_per_batch * batches
        entry_form = RunEntryForm(request.POST or None, product=product, batches=batches)
        preview = [{
            "material": line.material,
            "expected": line.quantity * batches,
            "unit": line.unit,
            "actual_field": entry_form[f"actual_{line.pk}"],
            "wastage_field": entry_form[f"wastage_{line.pk}"],
        } for line in entry_form.bom_lines]
        if request.method == "POST" and entry_form.is_valid():
            try:
                with transaction.atomic():
                    run = ProductionRun.objects.create(
                        reference=entry_form.cleaned_data["reference"],
                        date=entry_form.cleaned_data["date"],
                        product=product,
                        planned_quantity=expected_output,
                        completed_quantity=entry_form.cleaned_data["good_output"],
                        unit=product.item.base_unit,
                    )
                    for line in entry_form.bom_lines:
                        ProductionConsumption.objects.create(
                            run=run, material=line.material,
                            quantity=entry_form.cleaned_data[f"actual_{line.pk}"],
                            unit=line.unit,
                            wastage_quantity=entry_form.cleaned_data[f"wastage_{line.pk}"],
                        )
                    complete_production_run(run, posted_by=request.user)
            except (ValidationError, IntegrityError) as exc:
                entry_form.add_error(None, str(exc))
            else:
                messages.success(request, f"Run {run.reference} posted with {run.completed_quantity} good {run.unit}.")
                return redirect("run_detail", pk=run.pk)
    return render(request, "erp/run_form.html", {
        "plan_form": plan_form, "entry_form": entry_form,
        "preview": preview, "product": product,
        "batches": batches, "expected_output": expected_output,
    })


@supervisor_access
def run_detail(request, pk):
    run = get_object_or_404(ProductionRun.objects.select_related("product__item"), pk=pk)
    overhead_form = OverheadForm(request.POST or None, initial={
        "labour_cost": run.labour_cost, "power_cost": run.power_cost,
    })
    if request.method == "POST" and overhead_form.is_valid():
        try:
            update_run_overheads(
                run, labour_cost=overhead_form.cleaned_data["labour_cost"],
                power_cost=overhead_form.cleaned_data["power_cost"],
                changed_by=request.user,
            )
        except ValidationError as exc:
            overhead_form.add_error(None, str(exc))
        else:
            messages.success(request, "Overheads saved; cost per packet recalculated.")
            return redirect("run_detail", pk=run.pk)
    planned_base = convert_quantity(
        run.planned_quantity, run.unit, run.product.item.base_unit,
        packets_per_carton=run.product.packets_per_carton,
    )
    batches = planned_base / Decimal(run.product.expected_yield_per_batch)
    bom = {line.material_id: line for line in run.product.bom_lines.all()}
    rows = []
    for entry in run.consumptions.select_related("material__item"):
        line = bom.get(entry.material_id)
        rows.append({
            "entry": entry,
            "expected": entry.standard_quantity_snapshot if entry.standard_quantity_snapshot is not None else (line.quantity * batches if line else None),
            "expected_unit": entry.standard_unit_snapshot or (line.unit if line else ""),
            "variance": entry.variance_percent if entry.standard_quantity_snapshot is not None or line else None,
            "alert": entry.variance_alert if entry.standard_quantity_snapshot is not None or line else False,
        })
    return render(request, "erp/run_detail.html", {
        "run": run, "rows": rows, "overhead_form": overhead_form,
        "movements": run.stock_movements.select_related("item").order_by("item__sku"),
        "revisions": run.cost_revisions.select_related("changed_by")[:10],
        "audit_events": run.audit_events.select_related("actor"),
    })


@supervisor_access
def stock_summary(request):
    rows = []
    for item in Item.objects.filter(is_active=True).order_by("kind", "sku"):
        category = item.material.get_category_display() if item.kind == Item.Kind.MATERIAL and hasattr(item, "material") else item.get_kind_display()
        rows.append({
            "item": item,
            "category": category,
            "balance": StockMovement.objects.balance_for(item),
        })
    return render(request, "erp/stock_summary.html", {"rows": rows})
