from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_product_dimensions"),
    ]

    operations = [
        migrations.AddField(
            model_name="uniteconomicssettings",
            name="acquiring_percent",
            field=models.FloatField(default=2.5),
        ),
    ]
