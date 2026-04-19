from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_uniteconomicsettings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductSizePrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nm_id", models.BigIntegerField()),
                ("size_id", models.BigIntegerField()),
                ("chrt_id", models.BigIntegerField(blank=True, null=True)),
                ("vendor_code", models.CharField(blank=True, max_length=255, null=True)),
                ("tech_size_name", models.CharField(blank=True, max_length=100, null=True)),
                ("price", models.FloatField(blank=True, null=True)),
                ("discounted_price", models.FloatField(blank=True, null=True)),
                ("club_discounted_price", models.FloatField(blank=True, null=True)),
                ("currency_iso_code_4217", models.CharField(blank=True, max_length=16, null=True)),
                ("discount_percent", models.FloatField(blank=True, null=True)),
                ("club_discount_percent", models.FloatField(blank=True, null=True)),
                ("editable_size_price", models.BooleanField(default=False)),
                ("is_bad_turnover", models.BooleanField(blank=True, null=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "seller",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.selleraccount"),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["seller", "nm_id"], name="core_produc_seller__6d3838_idx"),
                    models.Index(fields=["seller", "vendor_code"], name="core_produc_seller__988218_idx"),
                    models.Index(fields=["seller", "updated_at"], name="core_produc_seller__396f3c_idx"),
                ],
                "unique_together": {("seller", "nm_id", "size_id")},
            },
        ),
    ]
