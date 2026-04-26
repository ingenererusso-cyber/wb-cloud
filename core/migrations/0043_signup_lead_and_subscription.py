from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_support_chat_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="SignupLead",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("email", models.EmailField(max_length=254, unique=True)),
                        ("full_name", models.CharField(max_length=255)),
                        ("password_hash", models.CharField(max_length=255)),
                        ("confirm_token", models.CharField(max_length=128, unique=True)),
                        ("expires_at", models.DateTimeField()),
                        ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "indexes": [
                            models.Index(fields=["email", "confirmed_at"], name="core_signup_email_7a953f_idx"),
                            models.Index(fields=["confirm_token"], name="core_signup_confirm_00444d_idx"),
                        ]
                    },
                ),
                migrations.CreateModel(
                    name="UserSubscription",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        (
                            "plan_code",
                            models.CharField(
                                choices=[("month_1", "1 месяц"), ("month_6", "6 месяцев"), ("month_12", "12 месяцев")],
                                default="month_1",
                                max_length=20,
                            ),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("trial", "Trial"),
                                    ("active", "Active"),
                                    ("past_due", "Past due"),
                                    ("expired", "Expired"),
                                    ("canceled", "Canceled"),
                                ],
                                default="trial",
                                max_length=20,
                            ),
                        ),
                        ("trial_started_at", models.DateTimeField(blank=True, null=True)),
                        ("trial_ends_at", models.DateTimeField(blank=True, null=True)),
                        ("paid_from", models.DateTimeField(blank=True, null=True)),
                        ("paid_to", models.DateTimeField(blank=True, null=True)),
                        ("access_expires_at", models.DateTimeField(blank=True, null=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription", to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        "indexes": [
                            models.Index(fields=["status", "access_expires_at"], name="core_usersub_status_a9ba87_idx"),
                            models.Index(fields=["plan_code"], name="core_usersub_plan_co_7979a5_idx"),
                        ]
                    },
                ),
            ],
        ),
    ]
