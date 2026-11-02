import io
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from core.context import acting_as
from learners.importer import ImportFailed, import_learners
from learners.models import Guardian, Learner

from . import factories as f

HEADER = "first_name,last_name,gender,date_of_birth,class,guardian_name,guardian_phone,relationship\n"


class Import(TestCase):
    def setUp(self):
        self.actor = f.academic_admin()
        for code, stream in (("4", "A"), ("2", "A")):
            f.school_class(code, stream)

    def run_import(self, rows, **kwargs):
        with acting_as(self.actor):
            return import_learners(self.actor, io.StringIO(HEADER + rows), **kwargs)

    def test_valid_file_registers_everyone(self):
        result = self.run_import(
            "Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n"
            "Mwila,Mwansa,M,02/07/2018,2a,Mary Mwansa,+260 97 123 4567,Mother\n"
        )
        self.assertEqual(len(result.learners), 2)
        self.assertEqual(Guardian.objects.count(), 1)  # same name and number: one guardian
        self.assertEqual(result.guardians_reused, 1)

    def test_one_bad_row_saves_nothing(self):
        with self.assertRaises(ImportFailed) as caught:
            self.run_import(
                "Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n"
                "Mwila,Mwansa,X,2018-07-02,9Z,Mary Mwansa,12345,Mother\n"
            )
        errors = " ".join(caught.exception.errors)
        self.assertIn("Line 3: gender", errors)
        self.assertIn("class '9Z'", errors)
        self.assertIn("not a valid Zambian mobile", errors)
        self.assertEqual(Learner.objects.count(), 0)

    def test_duplicate_within_file_saves_nothing(self):
        row = "Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n"
        with self.assertRaises(ImportFailed) as caught:
            self.run_import(row + row)
        self.assertIn("Line 3: looks like Chilufya Mwansa", caught.exception.errors[0])
        self.assertEqual(Learner.objects.count(), 0)

    def test_duplicates_can_be_overridden_with_reason(self):
        row = "Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n"
        result = self.run_import(row + row, override_reason="Confirmed twins")
        self.assertEqual(len(result.learners), 2)

    def test_shared_handset_different_name_makes_new_guardian(self):
        result = self.run_import(
            "Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n"
            "Joseph,Phiri,M,2018-01-01,2A,Grace Phiri,0971234567,Mother\n"
        )
        self.assertEqual(Guardian.objects.count(), 2)
        self.assertIn("also belongs to Mary Mwansa", result.warnings[0])

    def test_dry_run_saves_nothing(self):
        result = self.run_import("Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n", dry_run=True)
        self.assertEqual(len(result.learners), 1)
        self.assertEqual(Learner.objects.count(), 0)

    def test_sample_file_and_command(self):
        f.school_class("F1", "A")
        sample = Path(__file__).resolve().parent.parent / "samples" / "learners.csv"
        out = io.StringIO()
        call_command("import_learners", str(sample), "--as", self.actor.username, stdout=out)
        self.assertIn("Registered 3 learner(s)", out.getvalue())


class SetupSchool(TestCase):
    def test_creates_structure_and_is_rerunnable(self):
        out = io.StringIO()
        call_command("setup_school", "--year", "2030", stdout=out)
        call_command("setup_school", "--year", "2030", stdout=out)
        school = f.school()
        self.assertEqual(school.grades.count(), 11)
        self.assertEqual(list(school.grades.values_list("code", flat=True)), [*"1234567", "F1", "F2", "F3", "F4"])
        year = school.academic_years.get(name="2030")
        self.assertEqual(year.status, "current")
        self.assertEqual(year.terms.count(), 3)
