from django.db import migrations, models


def fill_model_type_from_payload(apps, schema_editor):
    Calc = apps.get_model("core", "ProductUnitEconomicsCalculation")
    for row in Calc.objects.all().only("id", "input_data"):
        input_data = row.input_data if isinstance(row.input_data, dict) else {}
        raw = str(input_data.get("model_type") or "").strip().lower()
        row.model_type = "fbs" if raw == "fbs" else "fbo"
        row.save(update_fields=["model_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_uniteconomicssettings_fbo_fbs_fulfillment"),
    ]

    operations = [
        migrations.AddField(
            model_name="productuniteconomicscalculation",
            name="model_type",
            field=models.CharField(default="fbo", max_length=10),
        ),
        migrations.RunPython(fill_model_type_from_payload, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="productuniteconomicscalculation",
            unique_together={("seller", "product", "model_type")},
        ),
        migrations.RemoveIndex(
            model_name="productuniteconomicscalculation",
            name="core_produc_seller__895d0a_idx",
        ),
        migrations.AddIndex(
            model_name="productuniteconomicscalculation",
            index=models.Index(fields=["seller", "product", "model_type"], name="core_produc_seller__ce8cf8_idx"),
        ),
    ]
