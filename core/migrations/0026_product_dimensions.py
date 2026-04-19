from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_product_subject_and_commissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="height_cm",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="length_cm",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="width_cm",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
