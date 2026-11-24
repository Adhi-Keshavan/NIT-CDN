import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .billing import issue_credit_note, issue_invoice, record_payment
from .ledger import approve_order, complete_production_run, dispatch_order, receive_purchase, update_run_overheads
from .models import AuditEvent, CompanyProfile, Customer, Invoice, InvoiceLine, Item, Material, Order, OrderLine, Product, ProductionConsumption, ProductionRun, Purchase, StockMovement
from .reports import build_report
from .units import Unit


class DayFourReportingAndAuditTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = get_user_model().objects.get(username="factory_admin")
        self.product = Product.objects.get(item__sku="MURUKKU-200G")
        self.product.hsn_code = "210690"
        self.product.save(update_fields=["hsn_code"])
        self.company = CompanyProfile.objects.create(
            pk=1, name="Test Foods", address="Chennai", state="Tamil Nadu",
            state_code="33", gstin="33ABCDE1234F1Z5",
        )

    def posted_invoice(self, *, dispatch=False):
        purchase = Purchase.objects.create(
            reference="DAY4-FLOUR", date=date(2026, 11, 2), supplier_name="Flour supplier",
            material=Material.objects.get(item__sku="MAT-FLOUR"), quantity=Decimal("60"),
            unit=Unit.KILOGRAM, unit_cost=Decimal("45"),
        )
        receive_purchase(purchase)
        run = ProductionRun.objects.create(
            reference="DAY4-RUN", date=date(2026, 11, 2), product=self.product,
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
        complete_production_run(run, posted_by=self.admin)
        update_run_overheads(run, labour_cost=Decimal("1200"), power_cost=Decimal("600"), changed_by=self.admin)
        run.refresh_from_db()
        order = Order.objects.create(
            reference="DAY4-ORDER", date=date(2026, 11, 3),
            customer=Customer.objects.get(code="RETAILER-A"), salesperson=self.admin,
        )
        OrderLine.objects.create(order=order, line_number=1, product=self.product,
                                 quantity=Decimal("150"), unit=Unit.PACKET, unit_price=Decimal("50"))
        approve_order(order, reviewed_by=self.admin)
        invoice = issue_invoice(order, company=self.company, issue_date=date(2026, 11, 3),
                                place_of_supply_state="Tamil Nadu", place_of_supply_code="33",
                                due_date=date(2026, 11, 10), issued_by=self.admin)
        if dispatch:
            dispatch_order(order)
        return run, order, invoice

    def test_benchmark_reports_filter_and_export(self):
        run, order, invoice = self.posted_invoice(dispatch=True)
        record_payment(invoice, reference="DAY4-PAY", date=date(2026, 11, 4),
                       amount=Decimal("4000"), recorded_by=self.admin)
        item = Item.objects.get(sku="MURUKKU-200G")
        self.assertEqual(StockMovement.objects.balance_for(item), Decimal("88"))
        self.assertEqual(invoice.balance_due, Decimal("3875.00"))
        self.assertEqual(run.cost_per_packet, Decimal("31.14"))
        self.assertEqual(run.yield_variance_percent, Decimal("-6"))
        self.assertEqual(run.consumptions.get(material__item__sku="MAT-OIL").variance_percent, Decimal("5"))
        self.assertEqual(build_report("stock", date(2026, 11, 1), date(2026, 11, 30), item=item)[2][0][-1], Decimal("88"))
        self.assertEqual(build_report("collections", date(2026, 11, 1), date(2026, 11, 30))[2][1][3], Decimal("3875.00"))
        self.assertEqual(build_report("collections", date(2026, 12, 1), date(2026, 12, 15))[2][1][6], Decimal("3875.00"))
        ledger_rows = build_report("ledger", date(2026, 11, 2), date(2026, 11, 30), item=item)[2]
        self.assertEqual(ledger_rows[0][-1], Decimal("50"))
        self.assertEqual(ledger_rows[-1][-1], Decimal("88"))

        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("report_index")), "Sales register")
        query = "?start=2026-11-01&end=2026-11-30"
        for kind in ("sales", "gst", "purchases", "stock", "ledger", "production", "collections"):
            with self.subTest(kind=kind):
                url = reverse("report_detail", args=[kind])
                self.assertEqual(self.client.get(url + query).status_code, 200)
                csv_response = self.client.get(url + query + "&format=csv")
                self.assertEqual(csv_response.status_code, 200)
                self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
                self.assertGreaterEqual(len(list(csv.reader(StringIO(csv_response.content.decode("utf-8-sig"))))), 2)
                pdf_response = self.client.get(url + query + "&format=pdf")
                self.assertEqual(pdf_response.status_code, 200)
                self.assertTrue(pdf_response.content.startswith(b"%PDF"))
        sales = self.client.get(reverse("report_detail", args=["sales"]) + query + "&format=csv").content.decode("utf-8-sig")
        self.assertIn("7500.00", sales)
        self.assertIn("7875.00", sales)
        production = self.client.get(reverse("report_detail", args=["production"]) + query + "&format=csv").content.decode("utf-8-sig")
        self.assertIn("MAT-OIL +5.00%", production)
        empty = self.client.get(reverse("report_detail", args=["sales"]) + "?start=2026-10-01&end=2026-10-31&format=csv")
        self.assertEqual(len(list(csv.reader(StringIO(empty.content.decode("utf-8-sig"))))), 1)
        self.client.force_login(get_user_model().objects.get(username="salesman_a"))
        self.assertEqual(self.client.get(reverse("report_index")).status_code, 403)

    def test_credit_note_and_audit_are_traceable_and_immutable(self):
        run, order, invoice = self.posted_invoice()
        self.assertEqual(list(run.audit_events.values_list("action", flat=True)), ["overheads_changed", "run_posted"])
        run.labour_cost = Decimal("1")
        with self.assertRaises(ValidationError):
            run.save(update_fields=["labour_cost"])
        run.refresh_from_db()
        self.assertEqual(list(invoice.audit_events.values_list("action", flat=True)), ["invoice_issued"])
        note = issue_credit_note(invoice, date=date(2026, 11, 4), reason="Customer cancelled", issued_by=self.admin)
        self.assertTrue(note.number.startswith("CN/2026-27/"))
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(note.amount, Decimal("7875.00"))
        self.assertEqual(invoice.audit_events.first().actor, self.admin)
        self.assertEqual(invoice.audit_events.first().action, "credit_note_issued")
        self.assertEqual(build_report("gst", date(2026, 11, 1), date(2026, 11, 30))[2][0][-1], Decimal("0.00"))
        self.assertEqual(StockMovement.objects.balance_for(self.product.item), Decimal("238"))
        for action in (
            lambda: issue_credit_note(invoice, date=date(2026, 11, 4), reason="again", issued_by=self.admin),
            lambda: dispatch_order(order),
            lambda: record_payment(invoice, reference="LATE", date=date(2026, 11, 5), amount=Decimal("1")),
            lambda: Invoice.objects.filter(pk=invoice.pk).update(amount=1),
            lambda: Invoice.objects.filter(pk=invoice.pk).delete(),
            lambda: AuditEvent.objects.filter(invoice=invoice).delete(),
            lambda: note.delete(),
            lambda: InvoiceLine.objects.create(invoice=invoice, line_number=2, description="Extra", hsn_code="210690", quantity=1, unit=Unit.PACKET, unit_price=1, taxable_value=1, gst_rate=5),
        ):
            with self.assertRaises(ValidationError):
                action()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("invoice_detail", args=[invoice.pk])), note.number)
        self.assertContains(self.client.get(reverse("credit_note_detail", args=[note.pk])), "Customer cancelled")
        pdf = self.client.get(reverse("credit_note_pdf", args=[note.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(self.client.get(reverse("credit_note_new", args=[invoice.pk])).status_code, 302)

    def test_credit_note_requires_reason_and_no_payments(self):
        _, _, invoice = self.posted_invoice()
        with self.assertRaises(ValidationError):
            issue_credit_note(invoice, date=date(2026, 11, 4), reason="  ", issued_by=self.admin)
        record_payment(invoice, reference="PARTIAL", date=date(2026, 11, 4), amount=Decimal("10"))
        with self.assertRaises(ValidationError):
            issue_credit_note(invoice, date=date(2026, 11, 5), reason="Customer cancelled", issued_by=self.admin)
