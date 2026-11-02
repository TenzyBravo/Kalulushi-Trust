from django.db import migrations


class Migration(migrations.Migration):
    """Audit records can be added but never changed or removed (design §7)."""

    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER audit_logs_append_only
                    BEFORE UPDATE OR DELETE ON audit_logs
                    FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();
            """,
            reverse_sql="""
                DROP TRIGGER audit_logs_append_only ON audit_logs;
                DROP FUNCTION audit_logs_append_only();
            """,
        ),
    ]
