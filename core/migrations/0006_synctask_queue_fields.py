from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_alter_testerfeedback_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="synctask",
            name="kind",
            field=models.CharField(
                choices=[("general", "General sync"), ("ads_full", "Ads full sync")],
                default="general",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="synctask",
            name="payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="synctask",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="synctask",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("error", "Error"),
                    ("canceled", "Canceled"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="synctask",
            index=models.Index(fields=["status", "created_at"], name="core_syncta_status_360e0f_idx"),
        ),
        migrations.AddIndex(
            model_name="synctask",
            index=models.Index(fields=["seller", "status", "created_at"], name="core_syncta_seller__574748_idx"),
        ),
        migrations.AddConstraint(
            model_name="synctask",
            constraint=models.UniqueConstraint(
                condition=models.Q(seller__isnull=False, status__in=["queued", "running"]),
                fields=("seller",),
                name="core_synctask_unique_active_per_seller",
            ),
        ),
    ]
