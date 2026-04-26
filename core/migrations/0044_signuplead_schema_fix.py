"""
Синхронизация схемы с моделью SignupLead.

0043 обновляла только state (SeparateDatabaseAndState + пустой database_operations),
поэтому в БД таблица могла отсутствовать или существовать без password_hash.
"""

from django.db import migrations


def _column_names(connection, table: str) -> set[str]:
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}
        if connection.vendor == "postgresql":
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                [table],
            )
            return {row[0] for row in cursor.fetchall()}
        if connection.vendor == "mysql":
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = %s
                """,
                [table],
            )
            return {row[0] for row in cursor.fetchall()}
    return set()


def fix_signuplead_schema(apps, schema_editor):
    connection = schema_editor.connection
    SignupLead = apps.get_model("core", "SignupLead")
    table = SignupLead._meta.db_table
    tables = set(connection.introspection.table_names())
    if table not in tables:
        schema_editor.create_model(SignupLead)
        return
    if "password_hash" in _column_names(connection, table):
        return
    qn = connection.ops.quote_name(table)
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute(
                f"ALTER TABLE {qn} ADD COLUMN password_hash text NOT NULL DEFAULT ''"
            )
        else:
            cursor.execute(
                f"ALTER TABLE {qn} ADD COLUMN password_hash varchar(255) NOT NULL DEFAULT ''"
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_signup_lead_and_subscription"),
    ]

    operations = [
        migrations.RunPython(fix_signuplead_schema, migrations.RunPython.noop),
    ]
