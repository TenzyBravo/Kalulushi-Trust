from django.conf import settings
from django.db import migrations


def create_school(apps, schema_editor):
    School = apps.get_model("core", "School")
    SchoolSettings = apps.get_model("core", "SchoolSettings")
    if not School.objects.exists():
        school = School.objects.create(name=settings.SCHOOL_NAME, code=settings.SCHOOL_CODE)
        SchoolSettings.objects.create(school=school)


class Migration(migrations.Migration):
    dependencies = [("core", "0002_audit_append_only")]
    operations = [migrations.RunPython(create_school, migrations.RunPython.noop)]
