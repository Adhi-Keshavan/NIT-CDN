"""Small accessible chart specifications used by report cards."""

from collections import defaultdict
from decimal import Decimal

PALETTE = ("#25845c", "#e0a33a", "#347ca5", "#8b6bb4", "#dc7156", "#54a9a0")


def _label(value):
    value = str(value)
    return value if len(value) <= 22 else value[:19] + "…"


def _display(value, prefix=""):
    return f"{prefix}{value:,.2f}" if isinstance(value, Decimal) or isinstance(value, float) else f"{prefix}{value:,}"


def bar_chart(title, subtitle, values, *, prefix="₹"):
    pairs = [(str(label), Decimal(str(value))) for label, value in values]
    ceiling = max((abs(value) for _, value in pairs), default=Decimal("0")) or Decimal("1")
    items = [{
        "label": label, "short_label": _label(label), "value": _display(value, prefix),
        "width": round(float(abs(value) * 100 / ceiling), 1),
        "negative": value < 0, "color": PALETTE[index % len(PALETTE)],
    } for index, (label, value) in enumerate(pairs)]
    return {"kind": "bar", "title": title, "subtitle": subtitle, "items": items}


def grouped_chart(title, subtitle, groups, series_names, *, prefix=""):
    flattened = [abs(Decimal(str(value))) for _, values in groups for value in values]
    ceiling = max(flattened, default=Decimal("0")) or Decimal("1")
    chart_groups = []
    for label, values in groups:
        chart_groups.append({"label": _label(label), "bars": [{
            "name": series_names[index], "value": _display(Decimal(str(value)), prefix),
            "height": max(2, round(float(abs(Decimal(str(value))) * 100 / ceiling), 1)),
            "color": PALETTE[index % len(PALETTE)],
        } for index, value in enumerate(values)]})
    return {"kind": "grouped", "title": title, "subtitle": subtitle,
            "series": [{"name": name, "color": PALETTE[i % len(PALETTE)]} for i, name in enumerate(series_names)],
            "groups": chart_groups}


def stacked_chart(title, subtitle, groups, series_names, *, empty_label="No values"):
    result = []
    for label, values in groups:
        positives = [max(Decimal("0"), Decimal(str(value))) for value in values]
        total = sum(positives, Decimal("0"))
        result.append({"label": _label(label), "total": _display(total), "empty": total == 0, "segments": [{
            "name": series_names[index], "value": _display(positives[index]),
            "width": round(float(positives[index] * 100 / total), 2) if total else 0,
            "color": PALETTE[index % len(PALETTE)],
        } for index in range(len(series_names))]})
    return {"kind": "stacked", "title": title, "subtitle": subtitle,
            "series": [{"name": name, "color": PALETTE[i % len(PALETTE)]} for i, name in enumerate(series_names)],
            "groups": result, "empty_label": empty_label}


def donut_chart(title, subtitle, values):
    values = [(str(name), max(Decimal("0"), Decimal(str(value)))) for name, value in values]
    total = sum((value for _, value in values), Decimal("0"))
    offset = 0.0
    segments = []
    for index, (name, value) in enumerate(values):
        percent = float(value * 100 / total) if total else 0
        segment = {"name": name, "value": _display(value), "percent": round(percent, 1),
                   "dasharray": f"{percent * 2.513:.2f} 251.30", "offset": round(offset * 2.513, 2),
                   "color": PALETTE[index % len(PALETTE)]}
        segments.append(segment)
        offset += percent
    return {"kind": "donut", "title": title, "subtitle": subtitle, "segments": segments,
            "total": _display(total)}


def line_chart(title, subtitle, values, *, suffix=""):
    points = [(str(label), Decimal(str(value))) for label, value in values]
    if len(points) > 20:
        step = (len(points) - 1) / 19
        points = [points[round(i * step)] for i in range(20)]
    if not points:
        points = [("No movements", Decimal("0"))]
    low = min(Decimal("0"), *(value for _, value in points))
    high = max(Decimal("0"), *(value for _, value in points))
    span = high - low or Decimal("1")
    coords = []
    dots = []
    for index, (label, value) in enumerate(points):
        x = 18 + (364 * index / max(1, len(points) - 1))
        y = 12 + float((high - value) * 150 / span)
        coords.append(f"{x:.1f},{y:.1f}")
        dots.append({"x": round(x, 1), "y": round(y, 1), "label": label,
                     "value": f"{_display(value)}{suffix}"})
    return {"kind": "line", "title": title, "subtitle": subtitle,
            "points": " ".join(coords), "dots": dots,
            "min_label": f"{_display(low)}{suffix}", "max_label": f"{_display(high)}{suffix}"}


def report_charts(kind, rows, *, item_selected=False):
    if kind == "sales":
        totals = defaultdict(Decimal)
        for row in rows:
            totals[row[2]] += Decimal(str(row[9]))
        top = sorted(totals.items(), key=lambda pair: abs(pair[1]), reverse=True)[:8]
        return [bar_chart("Net sales by customer", "Invoice totals less credit notes in this date range", top)]
    if kind == "gst":
        values = [(name, sum((Decimal(str(row[index])) for row in rows), Decimal("0")))
                  for name, index in (("CGST", 2), ("SGST", 3), ("IGST", 4))]
        return [donut_chart("Tax mix", "GST component totals for the selected dates", values)]
    if kind == "purchases":
        totals = defaultdict(Decimal)
        for row in rows:
            totals[row[2]] += Decimal(str(row[7]))
        return [bar_chart("Purchases by supplier", "Received value in the selected date range", sorted(totals.items(), key=lambda pair: pair[1], reverse=True)[:8])]
    if kind == "stock":
        groups = [(row[1], (row[3], row[4], row[5], row[6])) for row in rows[:10]]
        return [grouped_chart("Stock movement by item", "Opening, receipts, issues, and closing; each item keeps its own unit", groups,
                              ("Opening", "Receipts", "Issues", "Closing"))]
    if kind == "ledger":
        if not item_selected:
            return [{"kind": "empty", "title": "Balance trend", "subtitle": "Select one item above to chart its running balance.", "message": "A single item keeps the graph in one consistent unit."}]
        values = [(f"{row[0]:%d %b} · {row[3]}", row[7]) for row in rows]
        return [line_chart("Running stock balance", "Base-unit balance after each movement", values)]
    if kind == "production":
        output = grouped_chart("Expected versus good output", "Completed production runs", [(row[1], (row[3], row[4])) for row in rows[:12]], ("Expected", "Good"), prefix="")
        costs = stacked_chart("Cost per production run", "Material, labour, and gas/power", [(row[1], (row[8], row[9], row[10])) for row in rows[:12]], ("Material", "Labour", "Gas/power"))
        variance = bar_chart("Yield variance", "Positive is above plan; negative is below plan", [(row[1], row[5]) for row in rows[:12]], prefix="")
        for bar in variance["items"]:
            bar["value"] += "%"
        return [output, costs, variance]
    if kind == "collections":
        aging = stacked_chart("Outstanding by ageing", "Open receivables by customer", [(row[0], row[4:8]) for row in rows[:10]], ("Not due", "1–30 days", "31–60 days", "61+ days"), empty_label="No outstanding")
        collection = grouped_chart("Collected versus outstanding", "Amounts for customers in the report", [(row[0], (row[2], row[3])) for row in rows if row[2] or row[3]][:10], ("Collected", "Outstanding"))
        return [aging, collection]
    return []
