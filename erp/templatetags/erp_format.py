from django import template


register = template.Library()


@register.filter
def quantity(value):
    if value is None:
        return ""
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


@register.filter
def unit_label(value):
    return {"l": "L", "ml": "mL"}.get(value, value)
