from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .billing import financial_year, issue_invoice
from .ledger import approve_order, complete_production_run, receive_purchase
from .models import (
    CompanyProfile, Customer, Invoice, Item, Material, Order, OrderLine,
    Payment, Product, ProductionConsumption, ProductionRun, Purchase, StockMovement,
)
from .units import Unit


class DayThreeWorkflowTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark", file=Path(__file__).resolve().parent.parent / "data" / "benchmark.json")
        self.product = Product.objects.get(item__sku="MURUKKU-200G")
        self.product.hsn_code = "210690"  # Test-only illustrative HSN, not fixture master data.
        self.product.save(update_fields=["hsn_code"])
        self.company = CompanyProfile.objects.create(
            pk=1, name="Test Foods", address="1 Test Street, Chennai",
            state="Tamil Nadu", state_code="33", gstin="33ABCDE1234F1Z5",
        )
        self.admin = get_user_model().objects.get(username="factory_admin")
        self.sales_a = get_user_model().objects.get(username="salesman_a")
        self.sales_b = get_user_model().objects.get(username="salesman_b")

    def balance(self):
        return StockMovement.objects.balance_for(Item.objects.get(sku="MURUKKU-200G"))

    def prepare_238_packets(self):
        purchase = Purchase.objects.create(
            reference="PUR-DAY3", date=date(2026, 11, 2), supplier_name="Test supplier",
            material=Material.objects.get(item__sku="MAT-FLOUR"),
            quantity=Decimal("60"), unit=Unit.KILOGRAM, unit_cost=Decimal("45"),
        )
        receive_purchase(purchase)
        run = ProductionRun.objects.create(
            reference="RUN-DAY3", date=date(2026, 11, 2), product=self.product,
            planned_quantity=Decimal("200"), completed_quantity=Decimal("188"), unit=Unit.PACKET,
        )
        for sku, amount, unit, wasted in (
            ("MAT-FLOUR", "40", Unit.KILOGRAM, "0"),
            ("MAT-OIL", "10.5", Unit.LITRE, "0"),
            ("MAT-SPICE", "1.2", Unit.KILOGRAM, "0"),
            ("PKG-POUCH-200G", "194", Unit.PIECE, "6"),
            ("PKG-CARTON-20", "9", Unit.PIECE, "0"),
        ):
            ProductionConsumption.objects.create(
                run=run, material=Material.objects.get(item__sku=sku),
                quantity=Decimal(amount), unit=unit, wastage_quantity=Decimal(wasted),
            )
        complete_production_run(run)
        self.assertEqual(self.balance(), Decimal("238"))

    def order_payload(self, quantity, customer_code):
        return {
            "customer": Customer.objects.get(code=customer_code).pk,
            "lines-TOTAL_FORMS": "3", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.pk, "lines-0-quantity": str(quantity),
        }

    def submit_order(self, user, quantity, customer_code):
        self.client.force_login(user)
        response = self.client.post(reverse("sales_order_new"), self.order_payload(quantity, customer_code))
        self.assertEqual(response.status_code, 302, response.context)
        return Order.objects.latest("pk")

    def test_full_benchmark_through_sales_and_management_pages(self):
        self.prepare_238_packets()
        self.client.force_login(self.sales_a)
        self.assertContains(self.client.get(reverse("sales_order_new")), 'name="viewport"')
        retailer = self.submit_order(self.sales_a, 150, "RETAILER-A")
        self.assertEqual(retailer.salesperson, self.sales_a)
        self.assertEqual(retailer.lines.get().unit_price, Decimal("50"))
        self.assertContains(self.client.get(reverse("sales_order_detail", args=[retailer.pk])), "Draft")

        self.client.force_login(self.admin)
        review_url = reverse("order_review", args=[retailer.pk])
        self.assertContains(self.client.get(review_url), "238")
        self.assertEqual(self.client.post(review_url, {"action": "approve"}).status_code, 302)
        retailer.refresh_from_db()
        self.assertEqual(retailer.status, Order.Status.CONFIRMED)
        self.assertEqual(retailer.reviewed_by, self.admin)
        self.assertEqual(self.balance(), Decimal("238"))
        self.assertContains(self.client.get(reverse("invoice_issue", args=[retailer.pk])), "7500.00")

        issue_response = self.client.post(reverse("invoice_issue", args=[retailer.pk]), {
            "issue_date": timezone.localdate().isoformat(),
            "place_of_supply_state": "Tamil Nadu", "place_of_supply_code": "33",
        })
        self.assertEqual(issue_response.status_code, 302, issue_response.context)
        invoice = Invoice.objects.get(order=retailer)
        self.assertTrue(invoice.number.startswith(f"INV/{financial_year(timezone.localdate())}/"))
        self.assertEqual(invoice.taxable_value, Decimal("7500.00"))
        self.assertEqual(invoice.cgst_amount, Decimal("187.50"))
        self.assertEqual(invoice.sgst_amount, Decimal("187.50"))
        self.assertEqual(invoice.igst_amount, Decimal("0.00"))
        self.assertEqual(invoice.amount, Decimal("7875.00"))
        self.assertEqual(invoice.lines.get().hsn_code, "210690")
        self.assertContains(self.client.get(reverse("invoice_detail", args=[invoice.pk])), "7875.00")
        pdf = self.client.get(reverse("invoice_pdf", args=[invoice.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertGreater(len(pdf.content), 2000)

        self.product.selling_price = Decimal("99")
        self.product.hsn_code = "999999"
        self.product.save(update_fields=["selling_price", "hsn_code"])
        customer = retailer.customer
        customer.name = "Changed later"
        customer.save(update_fields=["name"])
        invoice.refresh_from_db()
        self.assertEqual(invoice.customer_name, "Retailer A")
        self.assertEqual(invoice.lines.get().unit_price, Decimal("50"))
        self.assertEqual(invoice.lines.get().hsn_code, "210690")
        self.assertEqual(invoice.amount, Decimal("7875.00"))
        invoice.amount = Decimal("1")
        with self.assertRaises(ValidationError):
            invoice.save()
        invoice.refresh_from_db()
        stored_line = invoice.lines.get()
        stored_line.unit_price = Decimal("1")
        with self.assertRaises(ValidationError):
            stored_line.save()

        self.assertEqual(self.client.post(review_url, {"action": "dispatch"}).status_code, 302)
        self.assertEqual(self.balance(), Decimal("88"))
        self.assertEqual(retailer.stock_movements.count(), 1)
        self.assertEqual(self.client.post(review_url, {"action": "dispatch"}).status_code, 302)
        self.assertEqual(retailer.stock_movements.count(), 1)
        self.assertEqual(self.balance(), Decimal("88"))

        payment_response = self.client.post(reverse("payment_new", args=[invoice.pk]), {
            "reference": "PAY-4000", "date": timezone.localdate().isoformat(),
            "amount": "4000", "method": "Bank transfer",
        })
        self.assertEqual(payment_response.status_code, 302)
        self.assertEqual(invoice.balance_due, Decimal("3875.00"))
        self.assertContains(self.client.get(reverse("invoice_detail", args=[invoice.pk])), "3875.00")
        self.assertContains(self.client.get(reverse("management")), "3875.00")

        # Price edits affect new orders, but stock shortage remains 32 packets.
        distributor = self.submit_order(self.sales_b, 120, "DISTRIBUTOR-B")
        self.client.force_login(self.admin)
        distributor_url = reverse("order_review", args=[distributor.pk])
        self.assertContains(self.client.get(distributor_url), "32")
        self.assertEqual(self.client.post(distributor_url, {"action": "approve"}).status_code, 200)
        distributor.refresh_from_db()
        self.assertEqual(distributor.status, Order.Status.DRAFT)

    def test_admin_can_edit_draft_and_reject_it(self):
        order = self.submit_order(self.sales_a, 150, "RETAILER-A")
        self.product.selling_price = Decimal("55")
        self.product.save(update_fields=["selling_price"])
        self.client.force_login(self.admin)
        payload = self.order_payload(149, "RETAILER-A")
        payload["lines-INITIAL_FORMS"] = "1"
        payload["lines-TOTAL_FORMS"] = "4"
        response = self.client.post(reverse("order_edit", args=[order.pk]), payload)
        self.assertEqual(response.status_code, 302, response.context)
        self.assertEqual(order.lines.get().quantity, Decimal("149"))
        self.assertEqual(order.lines.get().unit_price, Decimal("55"))
        self.assertEqual(self.client.post(reverse("order_review", args=[order.pk]), {"action": "reject", "reason": "Wrong delivery date"}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REJECTED)
        self.assertEqual(self.client.get(reverse("order_edit", args=[order.pk])).status_code, 403)
        self.client.force_login(self.sales_a)
        self.assertContains(self.client.get(reverse("sales_order_detail", args=[order.pk])), "Wrong delivery date")

    def test_interstate_igst_rounding_and_financial_year(self):
        self.assertEqual(financial_year(date(2026, 3, 31)), "2025-26")
        self.assertEqual(financial_year(date(2026, 4, 1)), "2026-27")
        self.product.selling_price = Decimal("0.99")
        self.product.save(update_fields=["selling_price"])
        for code, pos_state, pos_code in (
            ("RETAILER-A", "Tamil Nadu", "33"),
            ("DISTRIBUTOR-B", "Karnataka", "29"),
        ):
            order = Order.objects.create(
                reference=f"ROUND-{code}", date=date(2026, 11, 3),
                customer=Customer.objects.get(code=code), salesperson=self.sales_a,
            )
            OrderLine.objects.create(
                order=order, line_number=1, product=self.product,
                quantity=Decimal("1"), unit=Unit.PACKET, unit_price=Decimal("0.99"),
            )
            approve_order(order, reviewed_by=self.admin)
            invoice = issue_invoice(
                order, company=self.company, issue_date=date(2026, 11, 3),
                place_of_supply_state=pos_state, place_of_supply_code=pos_code,
            )
            if code == "RETAILER-A":
                self.assertEqual((invoice.cgst_amount, invoice.sgst_amount, invoice.igst_amount, invoice.amount),
                                 (Decimal("0.02"), Decimal("0.02"), Decimal("0.00"), Decimal("1.03")))
            else:
                self.assertEqual((invoice.cgst_amount, invoice.sgst_amount, invoice.igst_amount, invoice.amount),
                                 (Decimal("0.00"), Decimal("0.00"), Decimal("0.05"), Decimal("1.04")))
            with self.assertRaises(ValidationError):
                issue_invoice(order, company=self.company, issue_date=date(2026, 11, 3),
                              place_of_supply_state=pos_state, place_of_supply_code=pos_code)

    def test_role_restrictions_invoice_requirement_and_overpayment(self):
        self.prepare_238_packets()
        order = self.submit_order(self.sales_a, 150, "RETAILER-A")
        self.client.force_login(self.sales_a)
        for name in ("management", "company_profile"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.get(reverse("order_review", args=[order.pk])).status_code, 403)
        self.client.force_login(get_user_model().objects.get(username="production_supervisor"))
        self.assertEqual(self.client.get(reverse("sales_order_new")).status_code, 403)
        self.client.force_login(self.admin)
        review_url = reverse("order_review", args=[order.pk])
        self.assertEqual(self.client.post(review_url, {"action": "approve"}).status_code, 302)
        blocked = self.client.post(review_url, {"action": "dispatch"})
        self.assertContains(blocked, "Issue an invoice before dispatch")
        self.assertEqual(self.balance(), Decimal("238"))
        invoice = issue_invoice(order, company=self.company, issue_date=timezone.localdate(),
                                place_of_supply_state="Tamil Nadu", place_of_supply_code="33")
        self.client.force_login(self.sales_b)
        self.assertEqual(self.client.get(reverse("sales_order_detail", args=[order.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("invoice_detail", args=[invoice.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("invoice_pdf", args=[invoice.pk])).status_code, 404)
        self.client.force_login(self.sales_a)
        self.assertEqual(self.client.get(reverse("payment_new", args=[invoice.pk])).status_code, 403)
        self.client.force_login(self.admin)
        too_much = self.client.post(reverse("payment_new", args=[invoice.pk]), {
            "reference": "OVERPAY", "date": timezone.localdate().isoformat(),
            "amount": "8000", "method": "Cash",
        })
        self.assertEqual(too_much.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)

    def test_invoice_setup_requires_company_identity_and_product_hsn(self):
        self.company.delete()
        self.product.hsn_code = ""
        self.product.save(update_fields=["hsn_code"])
        order = Order.objects.create(
            reference="SETUP-TEST", date=timezone.localdate(),
            customer=Customer.objects.get(code="RETAILER-A"), salesperson=self.sales_a,
        )
        OrderLine.objects.create(
            order=order, line_number=1, product=self.product,
            quantity=Decimal("1"), unit=Unit.PACKET, unit_price=Decimal("50"),
        )
        approve_order(order, reviewed_by=self.admin)
        self.client.force_login(self.admin)
        url = reverse("invoice_issue", args=[order.pk])
        self.assertContains(self.client.get(url), "Set the company GST details")
        self.assertEqual(self.client.post(reverse("company_profile"), {
            "name": "Test Foods", "address": "Chennai", "state": "Tamil Nadu",
            "state_code": "33", "gstin": "33ABCDE1234F1Z5",
        }).status_code, 302)
        self.assertContains(self.client.get(url), "Set the HSN code")
        self.assertFalse(Invoice.objects.filter(order=order).exists())
