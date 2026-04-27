"""
Приводит физическую таблицу core_signuplead к актуальной модели.

Ранее в БД могли остаться legacy-колонки:
- email_verified_at
- converted_user_id (FK на auth_user с NO ACTION)

Из-за этого удаление пользователей через admin/ORM могло падать.
"""

from django.db import migrations


def rebuild_signuplead_table(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "sqlite":
        return

    SignupLead = apps.get_model("core", "SignupLead")
    table = SignupLead._meta.db_table
    tables = set(connection.introspection.table_names())
    if table not in tables:
        schema_editor.create_model(SignupLead)
        return

    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cursor.fetchall()}

    required = {
        "id",
        "email",
        "full_name",
        "password_hash",
        "confirm_token",
        "expires_at",
        "confirmed_at",
        "created_at",
        "updated_at",
    }
    if columns == required:
        return

    with connection.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys=OFF;")
        cursor.execute(
            """
            CREATE TABLE core_signuplead_new (
                id integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                email varchar(254) NOT NULL UNIQUE,
                full_name varchar(255) NOT NULL,
                password_hash text NOT NULL DEFAULT '',
                confirm_token text NOT NULL DEFAULT '',
                expires_at datetime NULL,
                confirmed_at datetime NULL,
                created_at datetime NOT NULL,
                updated_at datetime NOT NULL
            );
            """
        )
        cursor.execute(
            f"""
            INSERT INTO core_signuplead_new (
                id, email, full_name, password_hash, confirm_token, expires_at, confirmed_at, created_at, updated_at
            )
            SELECT
                id,
                email,
                full_name,
                COALESCE(password_hash, ''),
                COALESCE(confirm_token, ''),
                expires_at,
                confirmed_at,
                created_at,
                updated_at
            FROM {table};
            """
        )
        cursor.execute(f"DROP TABLE {table};")
        cursor.execute(f"ALTER TABLE core_signuplead_new RENAME TO {table};")
        cursor.execute("CREATE INDEX core_signup_email_7a953f_idx ON core_signuplead (email, confirmed_at);")
        cursor.execute("CREATE INDEX core_signup_confirm_00444d_idx ON core_signuplead (confirm_token);")
        cursor.execute("PRAGMA foreign_keys=ON;")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_signuplead_complete_schema_fix"),
    ]

    operations = [
        migrations.RunPython(rebuild_signuplead_table, migrations.RunPython.noop),
    ]
