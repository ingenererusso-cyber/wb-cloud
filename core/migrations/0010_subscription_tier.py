from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_billingpayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="usersubscription",
            name="tier_code",
            field=models.CharField(
                choices=[("read", "Чтение"), ("read_write", "Чтение + запись")],
                default="read",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="billingpayment",
            name="tier_code",
            field=models.CharField(
                choices=[("read", "Чтение"), ("read_write", "Чтение + запись")],
                default="read",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="promocode",
            name="applies_to_tier_codes",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="usersubscription",
            name="status",
            field=models.CharField(
                choices=[
                    ("trial", "Бесплатный доступ (чтение)"),
                    ("active", "Active"),
                    ("past_due", "Past due"),
                    ("expired", "Expired"),
                    ("canceled", "Canceled"),
                ],
                default="trial",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["tier_code"], name="core_usersub_tier_co_idx"),
        ),
    ]
