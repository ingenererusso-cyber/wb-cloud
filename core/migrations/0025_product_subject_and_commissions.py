from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_productsizeprice"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="subject_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="subject_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.CreateModel(
            name="WbCategoryCommission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("locale", models.CharField(default="ru", max_length=10)),
                ("subject_id", models.BigIntegerField()),
                ("subject_name", models.CharField(blank=True, max_length=255, null=True)),
                ("parent_id", models.BigIntegerField(blank=True, null=True)),
                ("parent_name", models.CharField(blank=True, max_length=255, null=True)),
                ("kgvp_booking", models.FloatField(blank=True, null=True)),
                ("kgvp_marketplace", models.FloatField(blank=True, null=True)),
                ("kgvp_pickup", models.FloatField(blank=True, null=True)),
                ("kgvp_supplier", models.FloatField(blank=True, null=True)),
                ("kgvp_supplier_express", models.FloatField(blank=True, null=True)),
                ("paid_storage_kgvp", models.FloatField(blank=True, null=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "seller",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.selleraccount"),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["seller", "subject_id"], name="core_wbcatc_seller__0d08cc_idx"),
                    models.Index(fields=["seller", "updated_at"], name="core_wbcatc_seller__6c79a9_idx"),
                ],
                "unique_together": {("seller", "locale", "subject_id")},
            },
        ),
    ]
