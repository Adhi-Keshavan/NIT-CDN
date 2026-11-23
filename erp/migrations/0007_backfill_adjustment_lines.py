from django.db import migrations


def backfill_adjustment_lines(apps, schema_editor):
    database = schema_editor.connection.alias
    Movement = apps.get_model("erp", "StockMovement")
    Line = apps.get_model("erp", "StockAdjustmentLine")
    for movement in Movement.objects.using(database).filter(source_type__in=["opening", "adjustment"]).iterator():
        if movement.adjustment_id is None:
            raise RuntimeError(f"Stock movement {movement.pk} has no adjustment document")
        line, created = Line.objects.using(database).get_or_create(
            adjustment_id=movement.adjustment_id,
            line_number=movement.line_number,
            defaults={
                "item_id": movement.item_id,
                "quantity": movement.quantity,
                "unit": movement.unit,
            },
        )
        if not created and (line.item_id != movement.item_id or line.quantity != movement.quantity or line.unit != movement.unit):
            raise RuntimeError(f"Conflicting stock adjustment line for movement {movement.pk}")


class Migration(migrations.Migration):
    dependencies = [("erp", "0006_stockadjustmentline")]

    operations = [migrations.RunPython(backfill_adjustment_lines, migrations.RunPython.noop)]
