from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class Unit(models.TextChoices):
    EACH = "each", "Each"
    PIECE = "piece", "Piece"
    PACKET = "packet", "Packet"
    CARTON = "carton", "Carton"
    GRAM = "g", "Gram"
    KILOGRAM = "kg", "Kilogram"
    MILLILITRE = "ml", "Millilitre"
    LITRE = "l", "Litre"


# Factors are expressed relative to g, ml, or each within a dimension.
UNIT_FACTORS = {
    Unit.EACH: ("count", Decimal("1")),
    Unit.PIECE: ("count", Decimal("1")),
    Unit.PACKET: ("count", Decimal("1")),
    Unit.GRAM: ("mass", Decimal("1")),
    Unit.KILOGRAM: ("mass", Decimal("1000")),
    Unit.MILLILITRE: ("volume", Decimal("1")),
    Unit.LITRE: ("volume", Decimal("1000")),
}


def convert_quantity(quantity, from_unit, to_unit, *, packets_per_carton=None):
    if from_unit == Unit.CARTON or to_unit == Unit.CARTON:
        if packets_per_carton is None or Decimal(str(packets_per_carton)) <= 0:
            raise ValidationError("A positive packets_per_carton is required for carton conversions.")
        if from_unit not in {Unit.CARTON, Unit.PACKET, Unit.EACH} or to_unit not in {Unit.CARTON, Unit.PACKET, Unit.EACH}:
            raise ValidationError(f"Cannot convert {from_unit} to {to_unit}.")
        factors = {Unit.CARTON: Decimal(str(packets_per_carton)), Unit.PACKET: Decimal("1"), Unit.EACH: Decimal("1")}
        return Decimal(str(quantity)) * factors[from_unit] / factors[to_unit]
    try:
        from_dimension, from_factor = UNIT_FACTORS[from_unit]
        to_dimension, to_factor = UNIT_FACTORS[to_unit]
    except KeyError as exc:
        raise ValidationError(f"Unsupported unit: {exc.args[0]}") from exc
    if from_dimension != to_dimension:
        raise ValidationError(f"Cannot convert {from_unit} to {to_unit}.")
    return Decimal(str(quantity)) * from_factor / to_factor
