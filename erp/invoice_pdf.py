"""Render an issued invoice from stored snapshot fields only."""

from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def invoice_pdf_bytes(invoice):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=20 * mm,
        title=f"Tax invoice {invoice.number}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="InvoiceTitle", parent=styles["Title"], fontSize=20, leading=24, textColor=colors.HexColor("#173c2f")))
    styles.add(ParagraphStyle(name="SmallRight", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=9, leading=13))
    styles.add(ParagraphStyle(name="SmallInvoice", parent=styles["Normal"], fontSize=9, leading=13))

    def para(value, style="SmallInvoice"):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), styles[style])

    def decimal_quantity(value):
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered

    story = [Paragraph("TAX INVOICE", styles["InvoiceTitle"]), Spacer(1, 5 * mm)]
    story.append(Table([
        [para(f"{invoice.supplier_name}\n{invoice.supplier_address}\nGSTIN: {invoice.supplier_gstin}\n{invoice.supplier_state} ({invoice.supplier_state_code})"),
         para(f"Invoice: {invoice.number}\nDate: {invoice.date}\nOrder: {invoice.order.reference}\nDue: {invoice.due_date or '-'}", "SmallRight")],
    ], colWidths=[105 * mm, 69 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    story.extend([Spacer(1, 7 * mm), Paragraph("Bill to", styles["Heading3"])])
    story.append(para(f"{invoice.customer_name}\n{invoice.customer_address}\nGSTIN: {invoice.customer_gstin or 'Unregistered'}\n{invoice.customer_state} ({invoice.customer_state_code})"))
    story.append(para(f"Place of supply: {invoice.place_of_supply_state} ({invoice.place_of_supply_code})"))
    story.append(Spacer(1, 7 * mm))

    rows = [["#", "Description / HSN", "Quantity", "Rate (INR)", "Taxable (INR)", "GST %"]]
    for line in invoice.lines.all():
        rows.append([
            str(line.line_number), para(f"{line.description}\nHSN: {line.hsn_code}"),
            f"{decimal_quantity(line.quantity)} {line.unit}", f"{line.unit_price:.2f}",
            f"{line.taxable_value:.2f}", f"{line.gst_rate:.2f}",
        ])
    table = Table(rows, colWidths=[9 * mm, 65 * mm, 30 * mm, 25 * mm, 29 * mm, 16 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9efe9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#173c2f")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bdc9be")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(table)
    story.append(Spacer(1, 6 * mm))
    summary = [
        ["Taxable value", f"INR {invoice.taxable_value:.2f}"],
        ["CGST", f"INR {invoice.cgst_amount:.2f}"],
        ["SGST", f"INR {invoice.sgst_amount:.2f}"],
        ["IGST", f"INR {invoice.igst_amount:.2f}"],
        ["Invoice total", f"INR {invoice.amount:.2f}"],
    ]
    totals = Table(summary, colWidths=[51 * mm, 43 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#173c2f")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(totals)
    story.extend([Spacer(1, 17 * mm), para("____________________________\nAuthorised signatory")])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#526156"))
        canvas.drawString(18 * mm, 12 * mm, f"Invoice {invoice.number}")
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def credit_note_pdf_bytes(note):
    """Render a full reversal using only the original immutable invoice snapshot."""
    invoice = note.invoice
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=20 * mm,
                            title=f"Credit note {note.number}")
    styles = getSampleStyleSheet()
    small = styles["Normal"]
    small.fontSize = 9
    small.leading = 13

    def para(value):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), small)

    story = [Paragraph("CREDIT NOTE", styles["Title"]), Spacer(1, 5 * mm)]
    story.append(Table([[
        para(f"{invoice.supplier_name}\n{invoice.supplier_address}\nGSTIN: {invoice.supplier_gstin}\n{invoice.supplier_state} ({invoice.supplier_state_code})"),
        para(f"Credit note: {note.number}\nDate: {note.date}\nOriginal invoice: {invoice.number}\nOriginal date: {invoice.date}"),
    ]], colWidths=[90 * mm, 84 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    story.extend([Spacer(1, 7 * mm), Paragraph("Credit to", styles["Heading3"]),
                  para(f"{invoice.customer_name}\n{invoice.customer_address}\nGSTIN: {invoice.customer_gstin or 'Unregistered'}\n{invoice.customer_state} ({invoice.customer_state_code})"),
                  para(f"Place of supply: {invoice.place_of_supply_state} ({invoice.place_of_supply_code})"),
                  para(f"Reason: {note.reason}"), Spacer(1, 7 * mm)])
    rows = [["Description / HSN", "Quantity", "Taxable (INR)", "GST %"]]
    for line in invoice.lines.all():
        rows.append([para(f"{line.description}\nHSN: {line.hsn_code}"),
                     f"{line.quantity} {line.unit}", f"{line.taxable_value:.2f}", f"{line.gst_rate:.2f}"])
    table = Table(rows, colWidths=[75 * mm, 35 * mm, 40 * mm, 24 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9efe9")),
                              ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bdc9be")),
                              ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.extend([table, Spacer(1, 7 * mm)])
    story.append(para(f"Taxable value: INR {note.taxable_value:.2f}\nCGST: INR {note.cgst_amount:.2f}\nSGST: INR {note.sgst_amount:.2f}\nIGST: INR {note.igst_amount:.2f}\nTotal credit: INR {note.amount:.2f}"))
    story.extend([Spacer(1, 17 * mm), para("____________________________\nAuthorised signatory")])
    doc.build(story)
    return buffer.getvalue()
