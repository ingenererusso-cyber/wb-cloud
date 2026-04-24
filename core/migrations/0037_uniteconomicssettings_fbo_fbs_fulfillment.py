from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_order_is_return"),
    ]

    operations = [
        migrations.AddField(
            model_name="uniteconomicssettings",
            name="fbo_fulfillment_cost_per_order",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="uniteconomicssettings",
            name="fbs_fulfillment_cost_per_order",
            field=models.FloatField(default=0.0),
        ),
    ]

