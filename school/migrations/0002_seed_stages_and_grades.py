from django.db import migrations

# Design §4 and §9: Grades 1–7 (Primary) and Forms 1–4 (Secondary).
GRADES = [("Primary", 1, f"Grade {n}", str(n), n) for n in range(1, 8)] + [
    ("Secondary", 2, f"Form {n}", f"F{n}", 7 + n) for n in range(1, 5)
]


def seed(apps, schema_editor):
    School = apps.get_model("core", "School")
    EducationStage = apps.get_model("school", "EducationStage")
    Grade = apps.get_model("school", "Grade")
    for school in School.objects.all():
        for stage_name, stage_seq, name, code, seq in GRADES:
            stage, _ = EducationStage.objects.get_or_create(school=school, name=stage_name, defaults={"sequence": stage_seq})
            Grade.objects.get_or_create(school=school, code=code, defaults={"stage": stage, "name": name, "sequence": seq})


class Migration(migrations.Migration):
    dependencies = [("school", "0001_initial"), ("core", "0003_seed_school")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
