"""Upsert benchmark records by stable business keys from a supplied JSON file."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from erp.models import BOMLine, Customer, Item, Material, Product, StockAdjustment, StockAdjustmentLine, StockMovement
from erp.units import Unit


REQUIRED_SECTIONS = ("users", "customers", "materials", "products", "bom", "opening_balances")


def amount(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommandError(f"{label} must be a decimal number") from exc
    if not result.is_finite():
        raise CommandError(f"{label} must be finite")
    return result


def required(row, key, section):
    value = row.get(key)
    if value is None or value == "":
        raise CommandError(f"{section} entry is missing {key}")
    return value


def validate_keys(rows, section, key):
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CommandError(f"{section} entries must be objects")
        value = required(row, key, section)
        if value in seen:
            raise CommandError(f"Duplicate {section} {key}: {value}")
        seen.add(value)


class Command(BaseCommand):
    help = "Idempotently load the supplied Section 5 benchmark JSON."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, default=Path("data/benchmark.json"), help="Path to the exact Section 5 benchmark JSON")

    def handle(self, *args, **options):
        path = options["file"]
        if not path.is_file():
            raise CommandError(f"Benchmark file not found: {path}. Add the Section 5 data first.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read benchmark file: {exc}") from exc
        if not isinstance(data, dict):
            raise CommandError("Benchmark JSON must be an object")
        for section in REQUIRED_SECTIONS:
            if not isinstance(data.get(section), list):
                raise CommandError(f"Benchmark JSON needs a {section} array")
        for section, key in (("users", "username"), ("customers", "code"), ("materials", "sku"), ("products", "sku"), ("opening_balances", "sku")):
            validate_keys(data[section], section, key)

        try:
            with transaction.atomic():
                self.seed(data)
        except (ValidationError, ValueError, KeyError) as exc:
            raise CommandError(f"Invalid benchmark data: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Benchmark data upserted successfully."))

    def seed(self, data):
        User = get_user_model()
        for row in data["users"]:
            username = required(row, "username", "users")
            password = required(row, "password", "users")
            role = required(row, "role", "users")
            if role not in User.Role.values:
                raise CommandError(f"Invalid role for {username}: {role}")
            user, created = User.objects.get_or_create(username=username)
            if not created and user.is_superuser:
                raise CommandError(f"Refusing to overwrite an existing superuser: {username}")
            user.role = role
            user.email = row.get("email", "")
            user.first_name = row.get("first_name", "")
            user.last_name = row.get("last_name", "")
            user.is_staff = role == User.Role.ADMIN
            # Benchmark accounts are demo logins, never Django superusers.
            user.is_superuser = False
            if not user.check_password(password):
                user.set_password(password)
            user.full_clean()
            user.save()

        for row in data["customers"]:
            customer, _ = Customer.objects.update_or_create(
                code=required(row, "code", "customers"),
                defaults={
                    "name": required(row, "name", "customers"),
                    "email": row.get("email", ""),
                    "phone": row.get("phone", ""),
                    "address": row.get("address", ""),
                    "state": row.get("state", ""),
                    "state_code": row.get("state_code", ""),
                    "gstin": row.get("gstin", ""),
                },
            )
            customer.full_clean()

        skus = set()
        for section, kind, model, price_field in (
            ("materials", Item.Kind.MATERIAL, Material, "standard_cost"),
            ("products", Item.Kind.PRODUCT, Product, "selling_price"),
        ):
            for row in data[section]:
                sku = required(row, "sku", section)
                if sku in skus:
                    raise CommandError(f"SKU occurs in both products and materials: {sku}")
                skus.add(sku)
                unit = required(row, "base_unit", section)
                if unit not in Unit.values:
                    raise CommandError(f"Invalid base_unit for {sku}: {unit}")
                item, _ = Item.objects.get_or_create(sku=sku, defaults={"kind": kind, "base_unit": unit, "name": required(row, "name", section)})
                if item.kind != kind:
                    raise CommandError(f"Existing SKU has a different item kind: {sku}")
                item.name = required(row, "name", section)
                item.base_unit = unit
                item.full_clean()
                item.save()
                detail_defaults = {price_field: amount(required(row, price_field, section), f"{sku} {price_field}")}
                if kind == Item.Kind.MATERIAL:
                    detail_defaults["category"] = row.get("category", Material.Category.RAW)
                else:
                    detail_defaults.update({
                        "expected_yield_per_batch": row.get("expected_yield_per_batch", 1),
                        "packets_per_carton": row.get("packets_per_carton"),
                        "gst_rate": amount(row.get("gst_rate", "0"), f"{sku} gst_rate"),
                        "hsn_code": row.get("hsn_code", ""),
                    })
                detail, _ = model.objects.update_or_create(
                    item=item,
                    defaults=detail_defaults,
                )
                detail.full_clean()

        bom_keys = set()
        for row in data["bom"]:
            product_sku = required(row, "product_sku", "bom")
            material_sku = required(row, "material_sku", "bom")
            key = (product_sku, material_sku)
            if key in bom_keys:
                raise CommandError(f"Duplicate BOM line: {product_sku}/{material_sku}")
            bom_keys.add(key)
            try:
                product = Product.objects.get(item__sku=product_sku)
                material = Material.objects.get(item__sku=material_sku)
            except (Product.DoesNotExist, Material.DoesNotExist) as exc:
                raise CommandError(f"Unknown BOM item: {product_sku}/{material_sku}") from exc
            line, _ = BOMLine.objects.update_or_create(
                product=product, material=material,
                defaults={
                    "quantity": amount(required(row, "quantity", "bom"), "BOM quantity"),
                    "unit": required(row, "unit", "bom"),
                },
            )
            line.full_clean()

        for row in data["opening_balances"]:
            sku = required(row, "sku", "opening_balances")
            try:
                item = Item.objects.get(sku=sku)
            except Item.DoesNotExist as exc:
                raise CommandError(f"Unknown opening balance SKU: {sku}") from exc
            try:
                movement_date = date.fromisoformat(required(row, "date", "opening_balances"))
            except (TypeError, ValueError) as exc:
                raise CommandError(f"Invalid opening balance date for {sku}") from exc
            adjustment, _ = StockAdjustment.objects.update_or_create(
                reference=f"benchmark:{sku}",
                defaults={"date": movement_date, "kind": StockAdjustment.Kind.OPENING, "note": "Section 5 opening balance"},
            )
            quantity = amount(required(row, "quantity", "opening_balances"), "Opening quantity")
            unit = required(row, "unit", "opening_balances")
            StockAdjustmentLine.objects.update_or_create(
                adjustment=adjustment, line_number=1,
                defaults={"item": item, "quantity": quantity, "unit": unit},
            )
            movement, _ = StockMovement.objects.update_or_create(
                item=item,
                source_type=StockMovement.Source.OPENING,
                source_reference=f"benchmark:{sku}",
                line_number=1,
                defaults={
                    "date": movement_date,
                    "quantity": quantity,
                    "unit": unit,
                    "adjustment": adjustment,
                    "note": "Section 5 benchmark opening balance",
                },
            )
            movement.full_clean()
