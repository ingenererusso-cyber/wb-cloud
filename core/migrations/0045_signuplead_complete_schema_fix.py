"""
Доводит реальную схему SignupLead до текущей модели.

В базе могла остаться старая таблица sign-up лидов с другим набором колонок,
потому что 0043 меняла только migration state, а не actual DB schema.
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

    columns = _column_names(connection, table)
    qn = connection.ops.quote_name(table)

    def add_sqlite_column(sql: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE {qn} ADD COLUMN {sql}")

    def add_default_column(name: str, sql_type: str, default_sql: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"ALTER TABLE {qn} ADD COLUMN {connection.ops.quote_name(name)} "
                f"{sql_type} NOT NULL DEFAULT {default_sql}"
            )

    if "password_hash" not in columns:
        if connection.vendor == "sqlite":
            add_sqlite_column("password_hash text NOT NULL DEFAULT ''")
        else:
            add_default_column("password_hash", "varchar(255)", "''")

    if "confirm_token" not in columns:
        if connection.vendor == "sqlite":
            add_sqlite_column("confirm_token text NOT NULL DEFAULT ''")
        else:
            add_default_column("confirm_token", "varchar(128)", "''")

    if "expires_at" not in columns:
        if connection.vendor == "sqlite":
            add_sqlite_column("expires_at datetime NULL")
        else:
            with connection.cursor() as cursor:
                cursor.execute(f"ALTER TABLE {qn} ADD COLUMN expires_at timestamp with time zone NULL")

    if "confirmed_at" not in columns:
        if connection.vendor == "sqlite":
            add_sqlite_column("confirmed_at datetime NULL")
        else:
            with connection.cursor() as cursor:
                cursor.execute(f"ALTER TABLE {qn} ADD COLUMN confirmed_at timestamp with time zone NULL")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_signuplead_schema_fix"),
    ]

    operations = [
        migrations.RunPython(fix_signuplead_schema, migrations.RunPython.noop),
    ]
