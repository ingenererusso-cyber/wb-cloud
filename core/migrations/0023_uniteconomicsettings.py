from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_productcardsize_sellerfbsstock"),
    ]

    operations = [
        migrations.CreateModel(
            name="UnitEconomicsSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("assumed_spp_percent", models.FloatField(default=25.0)),
                ("drr_percent", models.FloatField(default=10.0)),
                ("defect_percent", models.FloatField(default=1.0)),
                ("fulfillment_cost_per_order", models.FloatField(default=0.0)),
                ("usn_percent", models.FloatField(default=6.0)),
                ("vat_percent", models.FloatField(default=0.0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "seller",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="unit_economics_settings",
                        to="core.selleraccount",
                    ),
                ),
            ],
        ),
    ]
