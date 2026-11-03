from decimal import Decimal

from django.db import migrations

# Decision B1: the same percentage bands for both stages, version 1, editable by making a new version.
BANDS = [
    ("Distinction", "75", "100", True),
    ("Merit", "60", "74", True),
    ("Credit", "50", "59", True),
    ("Pass", "40", "49", True),
    ("Fail", "0", "39", False),
]
TYPES = ["Test", "Assignment", "Project", "Mid-term exam", "End-of-term exam"]


def seed(apps, schema_editor):
    School = apps.get_model("core", "School")
    EducationStage = apps.get_model("school", "EducationStage")
    GradingScheme = apps.get_model("assessment", "GradingScheme")
    GradeBand = apps.get_model("assessment", "GradeBand")
    AssessmentType = apps.get_model("assessment", "AssessmentType")
    for school in School.objects.all():
        for name in TYPES:
            AssessmentType.objects.get_or_create(school=school, name=name)
        for stage in EducationStage.objects.filter(school=school):
            scheme, created = GradingScheme.objects.get_or_create(
                school=school, stage=stage, name="Percentage grades", version=1,
                defaults={"decimal_places": 0, "missing_rule": "exclude_excused"},
            )
            if created:
                for label, low, high, is_pass in BANDS:
                    # Bands meet at whole percentages; with 0 decimal places 74.6 rounds to 75.
                    GradeBand.objects.create(scheme=scheme, label=label, min_percent=Decimal(low),
                                             max_percent=Decimal(high) + (Decimal("0.99") if high != "100" else 0),
                                             is_pass=is_pass)
                scheme.status = "active"  # after its bands: bands of an active scheme are frozen
                scheme.save(update_fields=["status"])


class Migration(migrations.Migration):
    dependencies = [("assessment", "0002_database_rules"), ("school", "0002_seed_stages_and_grades")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
