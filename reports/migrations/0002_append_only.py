from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION report_exports_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'report_exports is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER report_exports_no_update_or_delete
    BEFORE UPDATE OR DELETE ON report_exports
    FOR EACH ROW EXECUTE FUNCTION report_exports_append_only();
"""
REVERSE = """
DROP TRIGGER IF EXISTS report_exports_no_update_or_delete ON report_exports;
DROP FUNCTION IF EXISTS report_exports_append_only();
"""


class Migration(migrations.Migration):
    """The exports log is never changed (reports design section 3)."""

    dependencies = [("reports", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
