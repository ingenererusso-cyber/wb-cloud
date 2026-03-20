from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_realizationreportdetail"),
    ]

    operations = [
        migrations.CreateModel(
            name="WbAcceptanceCoefficient",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("coeff_date", models.DateField()),
                ("warehouse_id", models.BigIntegerField()),
                ("warehouse_name", models.CharField(blank=True, max_length=255, null=True)),
                ("box_type_id", models.IntegerField(blank=True, null=True)),
                ("coefficient", models.FloatField(blank=True, null=True)),
                ("allow_unload", models.BooleanField(default=False)),
                ("is_sorting_center", models.BooleanField(default=False)),
                ("storage_coef", models.FloatField(blank=True, null=True)),
                ("delivery_coef", models.FloatField(blank=True, null=True)),
                ("delivery_base_liter", models.FloatField(blank=True, null=True)),
                ("delivery_additional_liter", models.FloatField(blank=True, null=True)),
                ("storage_base_liter", models.FloatField(blank=True, null=True)),
                ("storage_additional_liter", models.FloatField(blank=True, null=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "seller",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.selleraccount"),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["seller", "coeff_date"], name="core_wbacce_seller__fab4f0_idx"),
                    models.Index(fields=["warehouse_id"], name="core_wbacce_warehou_a18850_idx"),
                    models.Index(fields=["warehouse_name"], name="core_wbacce_warehou_3fd925_idx"),
                    models.Index(fields=["box_type_id"], name="core_wbacce_box_typ_5a0fe4_idx"),
                ],
                "unique_together": {("seller", "coeff_date", "warehouse_id", "box_type_id")},
            },
        ),
    ]
