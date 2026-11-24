"""Moving weighted average valuation for raw and packaging materials."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError

from .models import StockMovement


PAISA = Decimal("0.01")
RATE_PRECISION = Decimal("0.000001")


def weighted_average_material_cost(material, as_of=None):
    """Return rupees per base unit after all movements through ``as_of``.

    Opening stock uses the material's opening standard cost. Purchases add their
    actual invoice value. Issues leave at the current average; positive manual
    adjustments enter at the current average (or opening rate if stock is zero).
    """
    movements = material.item.movements.select_related("purchase").order_by("date", "pk")
    if as_of is not None:
        movements = movements.filter(date__lte=as_of)
    quantity = Decimal("0")
    value = Decimal("0")
    for movement in movements:
        delta = movement.base_quantity
        if delta > 0:
            if movement.source_type == StockMovement.Source.OPENING:
                receipt_value = delta * material.standard_cost
            elif movement.source_type == StockMovement.Source.PURCHASE:
                receipt_value = movement.purchase.quantity * movement.purchase.unit_cost
            else:
                rate = value / quantity if quantity > 0 else material.standard_cost
                receipt_value = delta * rate
            quantity += delta
            value += receipt_value
        else:
            if quantity < -delta:
                raise ValidationError(f"Negative valued stock for {material.item.sku}.")
            average = value / quantity
            value += delta * average
            quantity += delta
            if quantity == 0:
                value = Decimal("0")
    return value / quantity if quantity > 0 else material.standard_cost


def material_issue_cost(material, quantity_in_base_units, as_of=None):
    rate = weighted_average_material_cost(material, as_of=as_of)
    extended = (rate * quantity_in_base_units).quantize(PAISA, rounding=ROUND_HALF_UP)
    return rate.quantize(RATE_PRECISION, rounding=ROUND_HALF_UP), extended
