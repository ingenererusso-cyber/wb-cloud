from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_order_is_buyout"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="buyout_date",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
