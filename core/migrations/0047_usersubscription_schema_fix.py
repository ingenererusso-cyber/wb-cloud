"""
Создает физическую таблицу UserSubscription, если она не была создана ранее.

0043 меняла migration state без database_operations, поэтому на чистой базе
таблица могла отсутствовать, хотя модель уже считалась примененной.
"""

from django.db import migrations


def ensure_usersubscription_table(apps, schema_editor):
    connection = schema_editor.connection
    UserSubscription = apps.get_model("core", "UserSubscription")
    table = UserSubscription._meta.db_table
    tables = set(connection.introspection.table_names())
    if table in tables:
        return
    schema_editor.create_model(UserSubscription)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_signuplead_drop_legacy_columns"),
    ]

    operations = [
        migrations.RunPython(ensure_usersubscription_table, migrations.RunPython.noop),
    ]
