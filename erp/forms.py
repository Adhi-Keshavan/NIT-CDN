from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Product, Purchase


class PurchaseReceiptForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ("reference", "date", "supplier_name", "material", "quantity", "unit", "unit_cost")
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
        labels = {"unit_cost": "Rate per selected unit (₹)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()
        self.fields["material"].queryset = self.fields["material"].queryset.select_related("item").order_by("item__name")


class RunPlanForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.select_related("item").order_by("item__name"))
    batches = forms.IntegerField(min_value=1, max_value=1000, initial=1, label="Number of batches")


class RunEntryForm(forms.Form):
    reference = forms.CharField(max_length=60, label="Run reference")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), initial=timezone.localdate)
    good_output = forms.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))

    def __init__(self, *args, product, batches, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.batches = batches
        self.bom_lines = list(product.bom_lines.select_related("material__item").order_by("material__item__name"))
        self.fields["good_output"].label = f"Good output ({product.item.base_unit})"
        self.fields["good_output"].initial = product.expected_yield_per_batch * batches
        for line in self.bom_lines:
            self.fields[f"actual_{line.pk}"] = forms.DecimalField(
                max_digits=18, decimal_places=6, min_value=Decimal("0.000001"),
                initial=line.quantity * batches,
                label=f"{line.material.item.name} issued ({line.unit})",
            )
            self.fields[f"wastage_{line.pk}"] = forms.DecimalField(
                max_digits=18, decimal_places=6, min_value=Decimal("0"),
                initial=0, label=f"{line.material.item.name} damaged/wasted ({line.unit})",
            )

    def clean(self):
        cleaned = super().clean()
        for line in self.bom_lines:
            actual = cleaned.get(f"actual_{line.pk}")
            wastage = cleaned.get(f"wastage_{line.pk}")
            if actual is not None and wastage is not None and wastage > actual:
                self.add_error(f"wastage_{line.pk}", "Wastage cannot exceed quantity issued.")
        return cleaned


class OverheadForm(forms.Form):
    labour_cost = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), label="Labour (₹)")
    power_cost = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), label="Gas and power (₹)")
