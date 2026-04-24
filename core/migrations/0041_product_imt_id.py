from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_order_buyout_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="imt_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
