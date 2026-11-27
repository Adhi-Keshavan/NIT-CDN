"""Admin report screens and matching CSV/PDF downloads."""

import csv
from datetime import date
from io import BytesIO
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from django import forms
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from accounts.models import User
from accounts.views import roles_required

from .models import Item
from .charts import report_charts
from .reports import REPORTS, build_report


class ReportFilterForm(forms.Form):
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    item = forms.ModelChoiceField(queryset=Item.objects.order_by("sku"), required=False, empty_label="All items")

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            self.add_error("end", "End date must be on or after start date.")
        return data


def _pdf(title, columns, rows, start, end):
    output = BytesIO()
    page_size = landscape(A3 if len(columns) > 9 else A4)
    document = SimpleDocTemplate(output, pagesize=page_size, leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=25)
    styles = getSampleStyleSheet()
    cell_style = styles["Normal"]
    cell_style.fontName = "Helvetica"
    cell_style.fontSize = 7 if len(columns) <= 8 else 6
    cell_style.leading = 9
    width = (page_size[0] - 50) / len(columns)
    data = [[Paragraph(escape(str(value)), cell_style) for value in columns]]
    data += [[Paragraph(escape(str(value if value is not None else "")), cell_style) for value in row] for row in rows]
    table = LongTable(data, colWidths=[width] * len(columns), repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcebdd")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d8cb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    document.build([Paragraph(escape(title), styles["Title"]), Paragraph(f"{start:%d %b %Y} to {end:%d %b %Y}", styles["Normal"]), Spacer(1, 12), table])
    return output.getvalue()


@roles_required(User.Role.ADMIN)
def report_index(request):
    today = timezone.localdate()
    initial = {"start": date(today.year, today.month, 1), "end": today}
    form = ReportFilterForm(request.GET if request.GET else initial)
    report_links = []
    query = ""
    visualization_url = ""
    if form.is_valid():
        start, end, item = (form.cleaned_data[key] for key in ("start", "end", "item"))
        query = urlencode({"start": start.isoformat(), "end": end.isoformat(), **({"item": item.pk} if item else {})})
        visualization_url = f"{reverse('report_visualizations')}?{query}"
        for key, (title, _) in REPORTS.items():
            report_links.append({
                "key": key, "title": title,
                "url": f"{reverse('report_detail', args=[key])}?{query}",
            })
    return render(request, "erp/report_index.html", {
        "reports": REPORTS, "form": form, "report_links": report_links,
        "visualization_url": visualization_url,
    })


@roles_required(User.Role.ADMIN)
def report_visualizations(request):
    today = timezone.localdate()
    initial = {"start": date(today.year, today.month, 1), "end": today}
    form = ReportFilterForm(request.GET if request.GET else initial)
    charts = []
    if form.is_valid():
        start, end, item = (form.cleaned_data[key] for key in ("start", "end", "item"))
        for kind in REPORTS:
            if kind == "ledger" and not item:
                rows = []
            else:
                _, _, rows = build_report(kind, start, end, item=item)
            charts.extend(report_charts(kind, rows, item_selected=bool(item)))
    return render(request, "erp/report_visualizations.html", {"form": form, "charts": charts})


@roles_required(User.Role.ADMIN)
def report_detail(request, kind):
    if kind not in REPORTS:
        raise Http404("Unknown report")
    today = timezone.localdate()
    initial = {"start": date(today.year, today.month, 1), "end": today}
    form = ReportFilterForm(request.GET if request.GET else initial)
    title, columns = REPORTS[kind]
    rows = []
    charts = []
    export_query = ""
    if form.is_valid():
        start, end, item = (form.cleaned_data[key] for key in ("start", "end", "item"))
        title, columns, rows = build_report(kind, start, end, item=item)
        charts = [] if kind == "collections" else report_charts(kind, rows, item_selected=bool(item))
        export_query = urlencode({"start": start.isoformat(), "end": end.isoformat(), **({"item": item.pk} if item else {})})
        output_format = request.GET.get("format")
        if output_format == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="{kind}-{start}-{end}.csv"'
            response.write("\ufeff")
            writer = csv.writer(response)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([f"'{value}" if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value for value in row])
            return response
        if output_format == "pdf":
            response = HttpResponse(_pdf(title, columns, rows, start, end), content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{kind}-{start}-{end}.pdf"'
            return response
    return render(request, "erp/report_detail.html", {
        "kind": kind, "title": title, "columns": columns, "rows": rows, "form": form,
        "show_item": kind in {"stock", "ledger"}, "export_query": export_query,
        "charts": charts,
    })
