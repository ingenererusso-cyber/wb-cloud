from django.db import migrations, models


def seed_is_buyout_from_existing_flags(apps, schema_editor):
    Order = apps.get_model("core", "Order")
    Order.objects.filter(is_cancel=False, is_return=False).update(is_buyout=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_productuniteconomicscalculation_model_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="is_buyout",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(seed_is_buyout_from_existing_flags, migrations.RunPython.noop),
    ]
