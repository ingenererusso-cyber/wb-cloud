from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Догоняющая миграция под уже внесённые (но не смигрированные) правки моделей
    billingpayment.status и индекса usersubscription. Не относится к ценовому роботу —
    вынесена отдельно, чтобы её можно было коммитить независимо.
    """

    dependencies = [
        ('core', '0010_subscription_tier'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='usersubscription',
            new_name='core_usersu_tier_co_067816_idx',
            old_name='core_usersub_tier_co_idx',
        ),
        migrations.AlterField(
            model_name='billingpayment',
            name='status',
            field=models.CharField(
                choices=[
                    ('created', 'Создан'),
                    ('init_failed', 'Ошибка инициализации'),
                    ('pending', 'Ожидает оплаты'),
                    ('authorized', 'Авторизован'),
                    ('confirmed', 'Оплачен'),
                    ('rejected', 'Отклонен'),
                    ('canceled', 'Отменен'),
                ],
                default='created',
                max_length=20,
            ),
        ),
    ]
