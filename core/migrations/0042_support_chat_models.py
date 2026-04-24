from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_product_imt_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Открыт"),
                            ("waiting_user", "Ждет пользователя"),
                            ("waiting_support", "Ждет поддержки"),
                            ("closed", "Закрыт"),
                        ],
                        default="open",
                        max_length=30,
                    ),
                ),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_threads", to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SupportMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("author_role", models.CharField(choices=[("user", "User"), ("support", "Support")], default="user", max_length=20)),
                ("body", models.TextField()),
                ("is_internal", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author_user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_messages", to=settings.AUTH_USER_MODEL),
                ),
                (
                    "thread",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="core.supportthread"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SupportThreadParticipantState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("unread_count", models.PositiveIntegerField(default=0)),
                ("last_read_at", models.DateTimeField(blank=True, null=True)),
                (
                    "thread",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="participant_states", to="core.supportthread"),
                ),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_thread_states", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "unique_together": {("thread", "user")},
            },
        ),
        migrations.AddIndex(
            model_name="supportthread",
            index=models.Index(fields=["user", "updated_at"], name="core_suppor_user_id_5f9607_idx"),
        ),
        migrations.AddIndex(
            model_name="supportthread",
            index=models.Index(fields=["status", "updated_at"], name="core_suppor_status_3a5789_idx"),
        ),
        migrations.AddIndex(
            model_name="supportmessage",
            index=models.Index(fields=["thread", "created_at"], name="core_suppor_thread__778300_idx"),
        ),
        migrations.AddIndex(
            model_name="supportmessage",
            index=models.Index(fields=["author_user", "created_at"], name="core_suppor_author__99f44a_idx"),
        ),
        migrations.AddIndex(
            model_name="supportthreadparticipantstate",
            index=models.Index(fields=["user", "unread_count"], name="core_suppor_user_id_b8a0f5_idx"),
        ),
        migrations.AddIndex(
            model_name="supportthreadparticipantstate",
            index=models.Index(fields=["thread", "user"], name="core_suppor_thread__f6e34d_idx"),
        ),
    ]
