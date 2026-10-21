import datetime

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase

from academics.models import GradingScale
from school.models import AcademicYear, ClassGroup, Grade, Level, SchoolSettings, Term

from .factories import make_structure, make_year


class SchoolSettingsTests(TestCase):
    def test_single_row(self):
        SchoolSettings.objects.create(name="First")
        SchoolSettings(name="Second").save()
        self.assertEqual(SchoolSettings.objects.count(), 1)
        self.assertEqual(SchoolSettings.load().name, "Second")


class AcademicYearTests(TestCase):
    def test_only_one_current_year(self):
        y1 = make_year("2025")
        y2 = make_year("2026")
        y1.refresh_from_db()
        self.assertFalse(y1.is_current)
        self.assertEqual(AcademicYear.current(), y2)

    def test_term_must_fall_inside_year(self):
        year = make_year("2026")
        term = Term(academic_year=year, number=1,
                    start_date=datetime.date(2025, 12, 1), end_date=datetime.date(2026, 4, 1))
        with self.assertRaises(ValidationError):
            term.full_clean()


class StructureTests(TestCase):
    def test_class_name_from_grade_and_stream(self):
        s = make_structure()
        self.assertEqual(s["classes"]["4A"].name, "4A")
        self.assertEqual(s["classes"]["8A"].level, s["secondary"])

    def test_class_unique_per_year(self):
        s = make_structure()
        with self.assertRaises(IntegrityError):
            ClassGroup.objects.create(grade=s["grades"][4], academic_year=s["year"], stream="A")

    def test_grade_names_are_data(self):
        level = Level.objects.create(name="Secondary")
        form1 = Grade.objects.create(level=level, name="Form 1", code="F1", order=8)
        self.assertEqual(str(form1), "Form 1")


class SeedSchoolTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_school", year=2026, stdout=open("/dev/null", "w"))
        call_command("seed_school", year=2026, stdout=open("/dev/null", "w"))
        self.assertEqual(Grade.objects.count(), 12)
        self.assertEqual(Term.objects.count(), 3)
        scale = GradingScale.objects.get()
        self.assertEqual(scale.check_bands(), [])
        self.assertEqual(scale.band_for(72).label, "2")
