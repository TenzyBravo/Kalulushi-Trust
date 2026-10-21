import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from academics.models import GradeBand, GradingScale
from school.models import AcademicYear, Grade, Level, Term

# ECZ secondary grading (Grade 12 / School Certificate).
ECZ_SECONDARY_BANDS = [
    ("1", "Distinction", 75, 100, True),
    ("2", "Distinction", 70, 74, True),
    ("3", "Merit", 65, 69, True),
    ("4", "Merit", 60, 64, True),
    ("5", "Credit", 55, 59, True),
    ("6", "Credit", 50, 54, True),
    ("7", "Satisfactory", 45, 49, True),
    ("8", "Satisfactory", 40, 44, True),
    ("9", "Unsatisfactory", 0, 39, False),
]


class Command(BaseCommand):
    help = "Create a starting school structure: levels, Grades 1-12, an academic year with three terms."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=datetime.date.today().year)
        parser.add_argument("--primary-only", action="store_true")
        parser.add_argument("--secondary-only", action="store_true")

    @transaction.atomic
    def handle(self, year, primary_only=False, secondary_only=False, **options):
        if not secondary_only:
            primary, _ = Level.objects.get_or_create(name="Primary", defaults={"order": 1})
            for n in range(1, 8):
                Grade.objects.get_or_create(code=str(n), defaults={"level": primary, "name": f"Grade {n}", "order": n})
        if not primary_only:
            secondary, _ = Level.objects.get_or_create(name="Secondary", defaults={"order": 2})
            for n in range(8, 13):
                Grade.objects.get_or_create(code=str(n), defaults={"level": secondary, "name": f"Grade {n}", "order": n})
            scale, created = GradingScale.objects.get_or_create(level=secondary, name="ECZ 1-9")
            if created:
                for label, desc, lo, hi, passed in ECZ_SECONDARY_BANDS:
                    GradeBand.objects.create(
                        scale=scale, label=label, description=desc, min_score=lo, max_score=hi, is_pass=passed
                    )

        academic_year, _ = AcademicYear.objects.get_or_create(
            name=str(year),
            defaults={
                "start_date": datetime.date(year, 1, 1),
                "end_date": datetime.date(year, 12, 31),
                "is_current": not AcademicYear.objects.filter(is_current=True).exists(),
            },
        )
        terms = [((1, 13), (4, 10)), ((5, 4), (8, 7)), ((9, 1), (12, 4))]
        for number, (start, end) in enumerate(terms, start=1):
            Term.objects.get_or_create(
                academic_year=academic_year,
                number=number,
                defaults={"start_date": datetime.date(year, *start), "end_date": datetime.date(year, *end)},
            )
        self.stdout.write(self.style.SUCCESS(
            f"School structure ready: {Level.objects.count()} level(s), {Grade.objects.count()} grade(s), "
            f"academic year {academic_year} with {academic_year.terms.count()} terms. "
            "Term dates are placeholders; set the real ones in the admin."
        ))
