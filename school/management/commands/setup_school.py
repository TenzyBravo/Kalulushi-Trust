"""Create the school and its fixed structure: Primary (Grades 1–7) and Secondary (Forms 1–4).

Safe to re-run: existing rows are left alone.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.context import acting_as
from school import services
from school.models import AcademicYear, EducationStage, Grade, School, SchoolSettings, Term

STAGES = [
    ("Primary", 1, [(f"Grade {n}", str(n)) for n in range(1, 8)]),
    ("Secondary", 2, [(f"Form {n}", f"F{n}") for n in range(1, 5)]),
]


class Command(BaseCommand):
    help = "Create the school, its stages and grades, and optionally an academic year with three terms."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Kalulushi Trust School")
        parser.add_argument("--code", default="KLT", help="Prefixes learner numbers. Cannot change later.")
        parser.add_argument(
            "--year", type=int, help="Also create this calendar year (January–December), with three terms, as current."
        )

    @transaction.atomic
    def handle(self, *args, name, code, year, **options):
        code = code.strip().upper()
        school = School.objects.filter(status="active").first()
        if school and school.code != code:
            raise CommandError(f"This installation already serves {school} ({school.code}).")
        if school is None:
            school = School.objects.create(name=name, code=code)
            self.stdout.write(f"Created school {school} ({code})")
        SchoolSettings.objects.get_or_create(school=school)

        sequence = 0
        for stage_name, stage_order, grades in STAGES:
            stage, _ = EducationStage.objects.get_or_create(
                school=school, name=stage_name, defaults={"sequence": stage_order}
            )
            for grade_name, grade_code in grades:
                sequence += 1
                Grade.objects.get_or_create(
                    school=school,
                    code=grade_code,
                    defaults={"stage": stage, "name": grade_name, "sequence": sequence},
                )
        self.stdout.write("Stages and grades ready: Grades 1–7 (Primary), Forms 1–4 (Secondary)")

        if year:
            self._create_year(school, year)

    def _create_year(self, school, year):
        academic_year, created = AcademicYear.objects.get_or_create(
            school=school,
            name=str(year),
            defaults={"start_date": datetime.date(year, 1, 1), "end_date": datetime.date(year, 12, 31)},
        )
        if created:
            # Typical Zambian term dates; adjust in the admin once the Ministry calendar is out.
            for number, (start, end) in enumerate(
                [((1, 13), (4, 10)), ((5, 5), (8, 7)), ((9, 1), (12, 4))], start=1
            ):
                Term.objects.create(
                    academic_year=academic_year,
                    number=number,
                    start_date=datetime.date(year, *start),
                    end_date=datetime.date(year, *end),
                )
            self.stdout.write(f"Created academic year {year} with three terms")
        if academic_year.status == "planned" and not AcademicYear.current_for(school):
            with acting_as():
                services.make_year_current(academic_year)
            self.stdout.write(f"{year} is now the current year")
