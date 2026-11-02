from django.db import migrations

# A class from another academic year is impossible (design §6, enrollments).
SQL = """
ALTER TABLE enrollments
    ADD CONSTRAINT enrollments_class_in_year_fk
    FOREIGN KEY (class_id, academic_year_id) REFERENCES classes (id, academic_year_id);
"""


class Migration(migrations.Migration):
    dependencies = [("learners", "0001_initial"), ("school", "0001_initial")]
    operations = [
        migrations.RunSQL(SQL, "ALTER TABLE enrollments DROP CONSTRAINT enrollments_class_in_year_fk;"),
    ]
