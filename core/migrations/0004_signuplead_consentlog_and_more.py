from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_wbsalefact"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="signuplead",
            name="marketing_consent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="signuplead",
            name="pdn_consent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="signuplead",
            name="pdn_consent_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.CreateModel(
            name="ConsentLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(db_index=True, max_length=254)),
                ("kind", models.CharField(max_length=32)),
                ("action", models.CharField(choices=[("grant", "Grant"), ("revoke", "Revoke")], default="grant", max_length=16)),
                ("document_version", models.CharField(max_length=32)),
                ("document_text_hash", models.CharField(blank=True, default="", max_length=64)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, default="", max_length=512)),
                ("source", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["email", "kind", "created_at"], name="core_consen_email_f14989_idx"),
                    models.Index(fields=["user", "kind", "created_at"], name="core_consen_user_id_2f764a_idx"),
                    models.Index(fields=["kind", "action", "created_at"], name="core_consen_kind_697dd2_idx"),
                ],
            },
        ),
    ]
