from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_signuplead_consentlog_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="testerfeedback",
            name="category",
            field=models.CharField(
                choices=[
                    ("bug", "Баг интерфейса"),
                    ("calc", "Неточность расчета"),
                    ("sync", "Проблема синхронизации"),
                    ("idea", "Идея/улучшение"),
                    ("account_delete_request", "Запрос на удаление аккаунта"),
                ],
                default="bug",
                max_length=32,
            ),
        ),
    ]
