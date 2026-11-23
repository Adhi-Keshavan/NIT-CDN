import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import BOMLine, Customer, Item, Material, Product, StockMovement
from .units import Unit, convert_quantity


SAMPLE_BENCHMARK = {
    "users": [
        {"username": "sales-test", "password": "TestPassword123!", "role": "salesman"},
        {"username": "admin-test", "password": "TestPassword123!", "role": "admin"},
        {"username": "production-test", "password": "TestPassword123!", "role": "production"},
    ],
    "customers": [{"code": "C-001", "name": "Example Customer"}],
    "materials": [{"sku": "M-001", "name": "Example Material", "base_unit": "kg", "standard_cost": "20.00"}],
    "products": [{"sku": "P-001", "name": "Example Product", "base_unit": "each", "selling_price": "50.00"}],
    "bom": [{"product_sku": "P-001", "material_sku": "M-001", "quantity": "500", "unit": "g"}],
    "opening_balances": [
        {"sku": "M-001", "quantity": "2", "unit": "kg", "date": "2026-01-01"},
        {"sku": "P-001", "quantity": "10", "unit": "each", "date": "2026-01-01"},
    ],
}


class UnitTests(TestCase):
    def test_conversions_and_dimension_validation(self):
        self.assertEqual(convert_quantity("1500", Unit.GRAM, Unit.KILOGRAM), Decimal("1.5"))
        self.assertEqual(convert_quantity("1.25", Unit.LITRE, Unit.MILLILITRE), Decimal("1250.00"))
        self.assertEqual(convert_quantity("2", Unit.CARTON, Unit.PACKET, packets_per_carton=20), Decimal("40"))
        with self.assertRaises(ValidationError):
            convert_quantity("2", Unit.CARTON, Unit.PACKET)
        with self.assertRaises(ValidationError):
            convert_quantity("1", Unit.EACH, Unit.KILOGRAM)


class LedgerTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(sku="M-001", name="Material", kind=Item.Kind.MATERIAL, base_unit=Unit.KILOGRAM)

    def test_opening_balance_and_dated_signed_movements(self):
        StockMovement.objects.create(
            item=self.item, quantity=Decimal("2"), unit=Unit.KILOGRAM,
            date=date(2026, 1, 1), source_type=StockMovement.Source.OPENING,
            source_reference="opening:M-001",
        )
        StockMovement.objects.create(
            item=self.item, quantity=Decimal("-500"), unit=Unit.GRAM,
            date=date(2026, 1, 2), source_type=StockMovement.Source.PRODUCTION,
            source_reference="RUN-001",
        )
        self.assertEqual(StockMovement.objects.balance_for(self.item, date(2026, 1, 1)), Decimal("2"))
        self.assertEqual(StockMovement.objects.balance_for(self.item), Decimal("1.5"))
        self.assertEqual(StockMovement.objects.get(source_type=StockMovement.Source.PRODUCTION).base_quantity, Decimal("-0.5"))

    def test_rejects_incompatible_unit_and_missing_source(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=self.item, quantity=1, unit=Unit.EACH, date=date.today(),
                source_type=StockMovement.Source.ADJUSTMENT, source_reference="ADJ-1",
            )
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=self.item, quantity=1, unit=Unit.KILOGRAM, date=date.today(),
                source_type=StockMovement.Source.ADJUSTMENT, source_reference="",
            )
        self.assertEqual(StockMovement.objects.count(), 0)


class BenchmarkSeedTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "benchmark.json"
        self.path.write_text(json.dumps(SAMPLE_BENCHMARK), encoding="utf-8")

    def test_seed_twice_is_idempotent_and_opening_balances_are_correct(self):
        call_command("seed_benchmark", file=self.path)
        call_command("seed_benchmark", file=self.path)
        self.assertEqual(get_user_model().objects.count(), 3)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Item.objects.count(), 2)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Material.objects.count(), 1)
        self.assertEqual(BOMLine.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 2)
        material_item = Item.objects.get(sku="M-001")
        self.assertEqual(StockMovement.objects.balance_for(material_item), Decimal("2"))
        self.assertEqual(StockMovement.objects.balance_for(Item.objects.get(sku="P-001")), Decimal("10"))

    def test_seed_updates_existing_business_keys(self):
        call_command("seed_benchmark", file=self.path)
        data = dict(SAMPLE_BENCHMARK)
        data["opening_balances"] = [dict(SAMPLE_BENCHMARK["opening_balances"][0], quantity="3"), SAMPLE_BENCHMARK["opening_balances"][1]]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        call_command("seed_benchmark", file=self.path)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(StockMovement.objects.balance_for(Item.objects.get(sku="M-001")), Decimal("3"))

    def test_all_three_roles_can_log_in_and_permissions_apply(self):
        call_command("seed_benchmark", file=self.path)
        for username, allowed, forbidden in (
            ("sales-test", "sales", "production"),
            ("production-test", "production", "sales"),
            ("admin-test", "management", None),
        ):
            self.client.logout()
            self.assertTrue(self.client.login(username=username, password="TestPassword123!"))
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
            self.assertEqual(self.client.get(reverse(allowed)).status_code, 200)
            if forbidden:
                self.assertEqual(self.client.get(reverse(forbidden)).status_code, 403)
        self.assertTrue(get_user_model().objects.get(username="admin-test").is_staff)
        self.assertFalse(get_user_model().objects.get(username="admin-test").is_superuser)


class AccountTests(TestCase):
    def test_login_page_renders(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")

    def test_createsuperuser_has_admin_business_role(self):
        user = get_user_model().objects.create_superuser("root-test", password="TestPassword123!")
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.is_staff)


class ExactBenchmarkTests(TestCase):
    def test_section_five_master_data_seeds_twice_without_duplicates(self):
        fixture = Path(__file__).resolve().parent.parent / "data" / "benchmark.json"
        call_command("seed_benchmark", file=fixture)
        call_command("seed_benchmark", file=fixture)
        self.assertEqual(get_user_model().objects.count(), 4)
        self.assertEqual(Customer.objects.count(), 2)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Material.objects.count(), 5)
        self.assertEqual(BOMLine.objects.count(), 5)
        self.assertEqual(StockMovement.objects.count(), 6)
        product = Product.objects.get(item__sku="MURUKKU-200G")
        self.assertEqual(product.expected_yield_per_batch, 200)
        self.assertEqual(product.selling_price, Decimal("50"))
        self.assertEqual(product.gst_rate, Decimal("5"))
        for username in ("salesman_a", "salesman_b", "factory_admin", "production_supervisor"):
            self.client.logout()
            self.assertTrue(self.client.login(username=username, password="BenchmarkDemo2026!"))
        self.assertEqual({line.material.item.sku: line.quantity for line in product.bom_lines.all()}, {
            "MAT-FLOUR": Decimal("40"),
            "MAT-OIL": Decimal("10"),
            "MAT-SPICE": Decimal("1.2"),
            "PKG-POUCH-200G": Decimal("200"),
            "PKG-CARTON-20": Decimal("10"),
        })
        for sku, expected in (
            ("MAT-FLOUR", "100"), ("MAT-OIL", "30"), ("MAT-SPICE", "5"),
            ("PKG-POUCH-200G", "500"), ("PKG-CARTON-20", "30"), ("MURUKKU-200G", "50"),
        ):
            self.assertEqual(StockMovement.objects.balance_for(Item.objects.get(sku=sku)), Decimal(expected))
