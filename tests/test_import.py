import io
import os
import tempfile

from django.core.management import CommandError, call_command
from django.test import TestCase

from academics.models import Enrollment
from people.importer import ImportFailed, import_students
from people.models import Guardian, Student

from .factories import make_structure

HEADER = "admission_number,first_name,last_name,gender,date_of_birth,class,guardian_name,guardian_phone,relationship\n"
GOOD = HEADER + (
    "2026-001,Chilufya,Mwansa,F,2016-03-14,4A,Mary Mwansa,0971234567,Mother\n"
    "2026-002,Mwila,Mwansa,M,02/07/2018,2A,Mary Mwansa,+260 97 123 4567,Mother\n"
    "2026-003,Natasha,Banda,female,,8A,Peter Banda,0961112233,Father\n"
)


def run(text, **kwargs):
    return import_students(io.StringIO(text), **kwargs)


class ImportTests(TestCase):
    def setUp(self):
        self.s = make_structure()

    def test_imports_pupils_guardians_and_classes(self):
        result = run(GOOD)
        self.assertEqual((result.created, result.updated, result.guardians_created), (3, 0, 2))
        self.assertEqual(Guardian.objects.count(), 2)
        mary = Guardian.objects.get(phone="+260971234567")
        self.assertEqual(mary.students.count(), 2)
        self.assertEqual(Enrollment.objects.get(student__admission_number="2026-003").class_group.name, "8A")
        self.assertEqual(Student.objects.get(admission_number="2026-001").primary_guardian, mary)

    def test_rerun_updates_not_duplicates(self):
        run(GOOD)
        result = run(GOOD.replace("Chilufya", "Chilu").replace("2026-003,Natasha,Banda,female,,8A", "2026-003,Natasha,Banda,F,,4A"))
        self.assertEqual((result.created, result.updated), (0, 3))
        self.assertEqual(Student.objects.count(), 3)
        self.assertEqual(Student.objects.get(admission_number="2026-001").first_name, "Chilu")
        self.assertEqual(Enrollment.objects.filter(student__admission_number="2026-003").get().class_group.name, "4A")

    def test_one_bad_row_saves_nothing(self):
        bad = GOOD + "2026-004,Bwalya,Zulu,X,,4A,Ann Zulu,123,Aunt\n"
        with self.assertRaises(ImportFailed) as ctx:
            run(bad)
        self.assertEqual(ctx.exception.errors, [
            "Line 5: gender must be F or M",
            "Line 5: guardian_phone '123' is not a valid Zambian mobile number",
        ])
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(Guardian.objects.count(), 0)

    def test_reports_unknown_class_duplicates_and_missing_columns(self):
        text = HEADER + (
            "A1,Ann,Phiri,F,,9Z,Jo Phiri,0971111111,\n"
            "A1,Ann,Phiri,F,,4A,Jo Phiri,0971111111,\n"
        )
        with self.assertRaises(ImportFailed) as ctx:
            run(text)
        self.assertIn("Line 2: class '9Z' does not exist for this academic year", ctx.exception.errors)
        self.assertIn("Line 3: admission_number A1 also appears on line 2", ctx.exception.errors)
        with self.assertRaises(ImportFailed) as ctx:
            run("admission_number,first_name\nA1,Ann\n")
        self.assertIn("Missing column(s)", ctx.exception.errors[0])

    def test_dry_run_saves_nothing(self):
        result = run(GOOD, dry_run=True)
        self.assertEqual(result.created, 3)
        self.assertEqual(Student.objects.count(), 0)

    def test_command_reports_errors(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as fh:
            fh.write(HEADER + "A1,Ann,Phiri,Q,,4A,Jo Phiri,123,\n")
        err = io.StringIO()
        try:
            with self.assertRaisesMessage(CommandError, "2 problem(s) found. Nothing was imported."):
                call_command("import_students", path, stderr=err, stdout=io.StringIO())
        finally:
            os.remove(path)
        self.assertIn("Line 2: gender must be F or M", err.getvalue())
