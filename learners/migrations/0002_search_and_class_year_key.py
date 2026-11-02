from django.db import migrations


class Migration(migrations.Migration):
    """Database rules the ORM cannot declare (design §6)."""

    dependencies = [("learners", "0001_initial"), ("school", "0001_initial")]

    operations = [
        # Name search and likely-duplicate checks.
        migrations.RunSQL(
            sql="CREATE INDEX learners_name_trgm ON learners USING gin (lower(first_name || ' ' || last_name) gin_trgm_ops);",
            reverse_sql="DROP INDEX learners_name_trgm;",
        ),
        # An enrolment's class must belong to the enrolment's academic year.
        migrations.RunSQL(
            sql="""
                ALTER TABLE enrollments ADD CONSTRAINT enrollments_class_in_year
                    FOREIGN KEY (class_id, academic_year_id) REFERENCES classes (id, academic_year_id);
            """,
            reverse_sql="ALTER TABLE enrollments DROP CONSTRAINT enrollments_class_in_year;",
        ),
    ]
