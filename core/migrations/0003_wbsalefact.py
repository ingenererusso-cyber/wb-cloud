from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_wbadvertstatdaily_day_sum"),
    ]

    operations = [
        migrations.CreateModel(
            name="WbSaleFact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sale_id", models.CharField(max_length=255)),
                ("srid", models.CharField(blank=True, default="", max_length=255)),
                ("nm_id", models.BigIntegerField(blank=True, null=True)),
                ("is_return", models.BooleanField(default=False)),
                ("is_buyout", models.BooleanField(default=False)),
                ("sale_date", models.DateTimeField()),
                ("last_change_date", models.DateTimeField(blank=True, null=True)),
                ("finished_price", models.FloatField(blank=True, null=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.selleraccount")),
            ],
            options={
                "unique_together": {("seller", "sale_id")},
            },
        ),
        migrations.AddIndex(
            model_name="wbsalefact",
            index=models.Index(fields=["seller", "sale_date"], name="core_wbsale_seller__4a6ea6_idx"),
        ),
        migrations.AddIndex(
            model_name="wbsalefact",
            index=models.Index(fields=["seller", "srid"], name="core_wbsale_seller__6a6609_idx"),
        ),
        migrations.AddIndex(
            model_name="wbsalefact",
            index=models.Index(fields=["seller", "is_buyout"], name="core_wbsale_seller__cfdf95_idx"),
        ),
    ]
