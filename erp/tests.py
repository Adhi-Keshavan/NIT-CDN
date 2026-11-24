import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .costing import weighted_average_material_cost
from .ledger import approve_order, complete_production_run, dispatch_order, post_adjustment, receive_purchase, stock_shortages_for_order, update_run_overheads
from .models import (
    BOMLine, Customer, Item, Material, Order, OrderLine, Product,
    ProductionConsumption, ProductionCostRevision, ProductionRun, Purchase, StockAdjustment, StockAdjustmentLine, StockMovement,
)
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
        self.opening = StockAdjustment.objects.create(reference="opening:M-001", date=date(2026, 1, 1), kind=StockAdjustment.Kind.OPENING)
        self.issue = StockAdjustment.objects.create(reference="ADJ-1", date=date(2026, 1, 2), kind=StockAdjustment.Kind.ADJUSTMENT)
        StockAdjustmentLine.objects.create(adjustment=self.opening, line_number=1, item=self.item, quantity=Decimal("2"), unit=Unit.KILOGRAM)
        StockAdjustmentLine.objects.create(adjustment=self.issue, line_number=1, item=self.item, quantity=Decimal("-500"), unit=Unit.GRAM)

    def test_opening_balance_and_dated_signed_movements(self):
        StockMovement.objects.create(
            item=self.item, quantity=Decimal("2"), unit=Unit.KILOGRAM,
            date=date(2026, 1, 1), source_type=StockMovement.Source.OPENING,
            source_reference="opening:M-001", adjustment=self.opening,
        )
        StockMovement.objects.create(
            item=self.item, quantity=Decimal("-500"), unit=Unit.GRAM,
            date=date(2026, 1, 2), source_type=StockMovement.Source.ADJUSTMENT,
            source_reference="ADJ-1", adjustment=self.issue,
        )
        self.assertEqual(StockMovement.objects.balance_for(self.item, date(2026, 1, 1)), Decimal("2"))
        self.assertEqual(StockMovement.objects.balance_for(self.item), Decimal("1.5"))
        self.assertEqual(StockMovement.objects.get(source_type=StockMovement.Source.ADJUSTMENT).base_quantity, Decimal("-0.5"))

    def test_rejects_incompatible_unit_and_missing_source(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=self.item, quantity=1, unit=Unit.EACH, date=date.today(),
                source_type=StockMovement.Source.ADJUSTMENT, source_reference="ADJ-1", adjustment=self.issue,
            )
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=self.item, quantity=1, unit=Unit.KILOGRAM, date=date.today(),
                source_type=StockMovement.Source.ADJUSTMENT, source_reference="", adjustment=self.issue,
            )
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_requires_a_real_matching_source_document(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=self.item, quantity=1, unit=Unit.KILOGRAM, date=date(2026, 1, 2),
                source_type=StockMovement.Source.PURCHASE, source_reference="PUR-1",
                adjustment=self.issue,
            )
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_database_rejects_a_movement_without_a_document(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockMovement.objects.bulk_create([StockMovement(
                    item=self.item, quantity=Decimal("1"), unit=Unit.KILOGRAM,
                    base_quantity=Decimal("1"), date=date(2026, 1, 2),
                    source_type=StockMovement.Source.ADJUSTMENT, source_reference="missing",
                )])

    def test_adjustment_posting_is_idempotent_and_lines_are_locked(self):
        post_adjustment(self.opening)
        post_adjustment(self.issue)
        post_adjustment(self.issue)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(StockMovement.objects.balance_for(self.item), Decimal("1.5"))
        line = self.issue.lines.get()
        line.quantity = Decimal("-600")
        with self.assertRaises(ValidationError):
            line.save()

    def test_item_base_unit_cannot_change_after_stock_is_posted(self):
        post_adjustment(self.opening)
        self.item.base_unit = Unit.GRAM
        with self.assertRaises(ValidationError):
            self.item.save()


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

    def test_seed_refuses_to_rewrite_posted_opening_stock(self):
        call_command("seed_benchmark", file=self.path)
        data = dict(SAMPLE_BENCHMARK)
        data["opening_balances"] = [dict(SAMPLE_BENCHMARK["opening_balances"][0], quantity="3"), SAMPLE_BENCHMARK["opening_balances"][1]]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(CommandError):
            call_command("seed_benchmark", file=self.path)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(StockMovement.objects.balance_for(Item.objects.get(sku="M-001")), Decimal("2"))

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


class PostingTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark", file=Path(__file__).resolve().parent.parent / "data" / "benchmark.json")

    def balance(self, sku):
        return StockMovement.objects.balance_for(Item.objects.get(sku=sku))

    def test_section_five_purchase_production_dispatch_and_shortage(self):
        flour = Material.objects.get(item__sku="MAT-FLOUR")
        purchase = Purchase.objects.create(
            reference="PUR-FLOUR-60", date=date(2026, 11, 2), supplier_name="Demo supplier",
            material=flour, quantity=Decimal("60"), unit=Unit.KILOGRAM,
            unit_cost=Decimal("45"),
        )
        receive_purchase(purchase)
        receive_purchase(purchase)
        self.assertEqual(purchase.stock_movements.count(), 1)
        self.assertEqual(self.balance("MAT-FLOUR"), Decimal("160"))
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=flour.item, quantity=Decimal("30"), unit=Unit.KILOGRAM,
                date=purchase.date, source_type=StockMovement.Source.PURCHASE,
                source_reference=purchase.reference, purchase=purchase, line_number=2,
            )

        product = Product.objects.get(item__sku="MURUKKU-200G")
        run = ProductionRun.objects.create(
            reference="RUN-MURUKKU-1", date=date(2026, 11, 2), product=product,
            planned_quantity=Decimal("200"), completed_quantity=Decimal("188"), unit=Unit.PACKET,
        )
        actuals = (
            ("MAT-FLOUR", "40", Unit.KILOGRAM, "0"),
            ("MAT-OIL", "10.5", Unit.LITRE, "0"),
            ("MAT-SPICE", "1.2", Unit.KILOGRAM, "0"),
            ("PKG-POUCH-200G", "194", Unit.PIECE, "6"),
            ("PKG-CARTON-20", "9", Unit.PIECE, "0"),
        )
        for sku, quantity, unit, wastage in actuals:
            ProductionConsumption.objects.create(
                run=run, material=Material.objects.get(item__sku=sku),
                quantity=Decimal(quantity), unit=unit, wastage_quantity=Decimal(wastage),
            )
        complete_production_run(run)
        complete_production_run(run)
        self.assertEqual(run.stock_movements.count(), 6)
        self.assertEqual(self.balance("MAT-FLOUR"), Decimal("120"))
        self.assertEqual(self.balance("MAT-OIL"), Decimal("19.5"))
        self.assertEqual(self.balance("PKG-POUCH-200G"), Decimal("306"))
        self.assertEqual(self.balance("MURUKKU-200G"), Decimal("238"))
        self.assertEqual(ProductionRun.objects.get(pk=run.pk).yield_variance_percent, Decimal("-6"))
        self.assertEqual(run.consumptions.get(material__item__sku="MAT-OIL").variance_percent, Decimal("5"))
        self.assertTrue(all(movement.source_document.pk == run.pk for movement in run.stock_movements.all()))

        retailer_order = Order.objects.create(
            reference="ORD-RETAILER-A", date=date(2026, 11, 3),
            customer=Customer.objects.get(code="RETAILER-A"),
            salesperson=get_user_model().objects.get(username="salesman_a"),
        )
        OrderLine.objects.create(
            order=retailer_order, line_number=1, product=product,
            quantity=Decimal("150"), unit=Unit.PACKET, unit_price=Decimal("50"),
        )
        approve_order(retailer_order)
        dispatch_order(retailer_order)
        dispatch_order(retailer_order)
        self.assertEqual(retailer_order.stock_movements.count(), 1)
        self.assertEqual(self.balance("MURUKKU-200G"), Decimal("88"))

        distributor_order = Order.objects.create(
            reference="ORD-DISTRIBUTOR-B", date=date(2026, 11, 3),
            customer=Customer.objects.get(code="DISTRIBUTOR-B"),
            salesperson=get_user_model().objects.get(username="salesman_b"),
        )
        OrderLine.objects.create(
            order=distributor_order, line_number=1, product=product,
            quantity=Decimal("120"), unit=Unit.PACKET, unit_price=Decimal("50"),
        )
        self.assertEqual(stock_shortages_for_order(distributor_order), {"MURUKKU-200G": Decimal("32")})
        with self.assertRaises(ValidationError):
            approve_order(distributor_order)
        self.assertEqual(Order.objects.get(pk=distributor_order.pk).status, Order.Status.DRAFT)

        purchase.quantity = Decimal("61")
        with self.assertRaises(ValidationError):
            purchase.save()
        posted_run = ProductionRun.objects.get(pk=run.pk)
        posted_run.completed_quantity = Decimal("190")
        with self.assertRaises(ValidationError):
            posted_run.save()
        oil_issue = run.consumptions.get(material__item__sku="MAT-OIL")
        oil_issue.quantity = Decimal("11")
        with self.assertRaises(ValidationError):
            oil_issue.save()
        posted_line = retailer_order.lines.get(line_number=1)
        posted_line.quantity = Decimal("151")
        with self.assertRaises(ValidationError):
            posted_line.save()
        dispatch_movement = retailer_order.stock_movements.get()
        dispatch_movement.quantity = Decimal("-149")
        with self.assertRaises(ValidationError):
            dispatch_movement.save()


class DayTwoProductionTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark", file=Path(__file__).resolve().parent.parent / "data" / "benchmark.json")
        self.product = Product.objects.get(item__sku="MURUKKU-200G")
        self.supervisor = get_user_model().objects.get(username="production_supervisor")

    def balance(self, sku):
        return StockMovement.objects.balance_for(Item.objects.get(sku=sku))

    def purchase_flour(self):
        purchase = Purchase.objects.create(
            reference="PUR-DAY2-60", date=date(2026, 11, 2), supplier_name="Flour supplier",
            material=Material.objects.get(item__sku="MAT-FLOUR"),
            quantity=Decimal("60"), unit=Unit.KILOGRAM, unit_cost=Decimal("45"),
        )
        receive_purchase(purchase)
        return purchase

    def run_payload(self, **changes):
        quantities = {
            "MAT-FLOUR": ("40", "0"), "MAT-OIL": ("10.5", "0"),
            "MAT-SPICE": ("1.2", "0"), "PKG-POUCH-200G": ("194", "6"),
            "PKG-CARTON-20": ("9", "0"),
        }
        payload = {
            "product": self.product.pk, "batches": "1", "reference": "RUN-DAY2",
            "date": "2026-11-02", "good_output": "188",
        }
        for line in self.product.bom_lines.select_related("material__item"):
            actual, wasted = quantities[line.material.item.sku]
            payload[f"actual_{line.pk}"] = actual
            payload[f"wastage_{line.pk}"] = wasted
        payload.update(changes)
        return payload

    def test_benchmark_posting_variance_and_weighted_cost(self):
        purchase = self.purchase_flour()
        self.assertEqual(self.balance("MAT-FLOUR"), Decimal("160"))
        self.assertEqual(purchase.stock_movements.get().source_document, purchase)
        flour = purchase.material
        self.assertEqual(weighted_average_material_cost(flour), Decimal("43.125"))

        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("run_new"), {"product": self.product.pk, "batches": 1})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "40 kg")
        self.assertContains(response, "10 L")
        self.assertContains(response, "1.2 kg")
        self.assertContains(response, "200 packet")
        response = self.client.post(reverse("run_new"), self.run_payload())
        self.assertEqual(response.status_code, 302)
        run = ProductionRun.objects.get(reference="RUN-DAY2")
        self.assertEqual(run.stock_movements.count(), 6)
        complete_production_run(run)
        self.assertEqual(run.stock_movements.count(), 6)
        duplicate = self.client.post(reverse("run_new"), self.run_payload())
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(ProductionRun.objects.filter(reference="RUN-DAY2").count(), 1)
        self.assertEqual(run.stock_movements.count(), 6)
        self.assertEqual(self.balance("MAT-FLOUR"), Decimal("120"))
        self.assertEqual(self.balance("MAT-OIL"), Decimal("19.5"))
        self.assertEqual(self.balance("PKG-POUCH-200G"), Decimal("306"))
        self.assertEqual(self.balance("MURUKKU-200G"), Decimal("238"))
        self.assertTrue(all(move.source_document.pk == run.pk for move in run.stock_movements.all()))

        self.assertEqual(run.yield_variance_percent, Decimal("-6"))
        self.assertTrue(run.yield_variance_alert)
        oil = run.consumptions.get(material__item__sku="MAT-OIL")
        self.assertEqual(oil.variance_percent, Decimal("5"))
        self.assertTrue(oil.variance_alert)
        self.assertEqual(oil.standard_quantity_snapshot, Decimal("10"))
        oil_bom = self.product.bom_lines.get(material=oil.material)
        oil_bom.quantity = Decimal("11")
        oil_bom.save()
        oil.refresh_from_db()
        self.assertEqual(oil.variance_percent, Decimal("5"))
        self.assertEqual(run.consumptions.get(material=flour).unit_cost_snapshot, Decimal("43.125000"))
        self.assertEqual(run.material_cost, Decimal("4055.00"))
        self.assertEqual(run.cost_per_packet, Decimal("21.57"))

        response = self.client.post(reverse("run_detail", args=[run.pk]), {
            "labour_cost": "1200", "power_cost": "600",
        })
        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.total_cost, Decimal("5855.00"))
        self.assertEqual(run.cost_per_packet, Decimal("31.14"))
        self.assertEqual(run.margin_per_packet, Decimal("18.86"))
        self.assertEqual(ProductionCostRevision.objects.get(run=run).changed_by, self.supervisor)
        update_run_overheads(run, labour_cost=Decimal("1200"), power_cost=Decimal("600"), changed_by=self.supervisor)
        self.assertEqual(ProductionCostRevision.objects.filter(run=run).count(), 1)
        self.assertContains(self.client.get(reverse("run_detail", args=[run.pk])), "Over 3% tolerance")

    def test_insufficient_material_rolls_back_run_and_ledger(self):
        self.client.force_login(self.supervisor)
        before_movements = StockMovement.objects.count()
        flour_line = self.product.bom_lines.get(material__item__sku="MAT-FLOUR")
        response = self.client.post(reverse("run_new"), self.run_payload(**{f"actual_{flour_line.pk}": "101"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Insufficient")
        self.assertFalse(ProductionRun.objects.filter(reference="RUN-DAY2").exists())
        self.assertEqual(StockMovement.objects.count(), before_movements)

    def test_mixed_issue_units_convert_to_base_stock_and_cost(self):
        self.purchase_flour()
        run = ProductionRun.objects.create(
            reference="RUN-MIXED-UNITS", date=date(2026, 11, 2), product=self.product,
            planned_quantity=Decimal("200"), completed_quantity=Decimal("188"), unit=Unit.PACKET,
        )
        actuals = {
            "MAT-FLOUR": ("40000", Unit.GRAM),
            "MAT-OIL": ("10500", Unit.MILLILITRE),
            "MAT-SPICE": ("1200", Unit.GRAM),
            "PKG-POUCH-200G": ("194", Unit.PIECE),
            "PKG-CARTON-20": ("9", Unit.PIECE),
        }
        for sku, (quantity, unit) in actuals.items():
            ProductionConsumption.objects.create(
                run=run, material=Material.objects.get(item__sku=sku),
                quantity=Decimal(quantity), unit=unit,
            )
        complete_production_run(run)
        self.assertEqual(self.balance("MAT-FLOUR"), Decimal("120"))
        self.assertEqual(self.balance("MAT-OIL"), Decimal("19.5"))
        self.assertEqual(run.material_cost, Decimal("4055.00"))
        self.assertEqual(run.consumptions.get(material__item__sku="MAT-OIL").variance_percent, Decimal("5"))

    def test_supervisor_purchase_form_and_role_access(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse("purchase_new"), {
            "reference": "PUR-DAY2-60", "date": "2026-11-02", "supplier_name": "Flour supplier",
            "material": Material.objects.get(item__sku="MAT-FLOUR").pk,
            "quantity": "60", "unit": Unit.KILOGRAM, "unit_cost": "45",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Purchase.objects.get(reference="PUR-DAY2-60").stock_movements.count(), 1)
        self.assertContains(self.client.get(reverse("stock_summary")), "160")
        self.client.force_login(get_user_model().objects.get(username="salesman_a"))
        for url_name in ("production", "purchase_new", "run_new", "stock_summary"):
            self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)
