"""Uploading a year's school calendar (terms, holidays, no-learner and no-alert days, events)."""
import datetime
import io
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts import roles
from attendance import calendar_import, services
from attendance.calendar import for_days
from attendance.models import AbsenceCase, CalendarEvent
from core.models import AuditLog
from school.models import Term
from staffing import services as staffing

from . import factories as f

D = datetime.date
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "school_calendar_2026.xlsx"
TODAY = D(2026, 1, 20)
_real_localdate = timezone.localdate


def fake_localdate(value=None, timezone=None):
    return TODAY if value is None else _real_localdate(value, timezone)


def csv_file(*rows):
    text = "Type,Name,Start date,End date,Applies to,Notes\n" + "\n".join(",".join(r) for r in rows) + "\n"
    return io.BytesIO(text.encode()), "calendar.csv"


class CalendarUploadTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", new=fake_localdate)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = f.admin_user()
        self.year = f.year()

    def upload_sample(self):
        with open(SAMPLE, "rb") as fh:
            preview = calendar_import.parse(fh, SAMPLE.name, self.year)
        return preview, calendar_import.apply(preview, self.admin)


class SampleCalendar(CalendarUploadTestCase):
    def test_sample_2026_calendar_is_valid(self):
        with open(SAMPLE, "rb") as fh:
            preview = calendar_import.parse(fh, SAMPLE.name, self.year)
        self.assertEqual(preview.errors, [])
        self.assertEqual([n for n, old, _ in preview.term_changes], [1, 2, 3])
        counts = preview.counts()
        self.assertEqual((counts["Public holiday"], counts["No absence alerts"], counts["No learners"]), (16, 8, 4))
        warned = {r.name for r in preview.warnings}
        self.assertIn("Teacher work days", warned)  # after Term 3: no effect on registers

    def test_applying_sets_terms_and_school_days(self):
        _, (created, replaced) = self.upload_sample()
        self.assertEqual((created, replaced), (44, 0))
        self.assertEqual(list(Term.objects.filter(academic_year=self.year).values_list("start_date", flat=True)),
                         [D(2026, 1, 12), D(2026, 5, 11), D(2026, 9, 7)])
        cal = for_days(D(2026, 1, 1), D(2026, 12, 31))
        self.assertFalse(cal.is_school_day(D(2026, 3, 12)))   # Youth Day
        self.assertFalse(cal.is_school_day(D(2026, 1, 9)))    # before term (staff development)
        self.assertTrue(cal.is_school_day(D(2026, 1, 19)))    # parent-teacher conference: learners come
        self.assertIsNotNone(cal.no_alert_day(D(2026, 1, 19)))
        self.assertTrue(cal.is_school_day(D(2026, 3, 30)))    # exams are ordinary school days
        self.assertTrue(AuditLog.objects.filter(action="calendar.upload").exists())


class NoAlertDays(CalendarUploadTestCase):
    def setUp(self):
        super().setUp()
        self.upload_sample()
        self.teacher = f.make_user("teacher", roles.TEACHER)
        self.c5a = f.make_class("5")
        staffing.assign_class_teacher(self.c5a, self.teacher, D(2026, 1, 12), self.admin)
        self.anna = f.register(self.admin, self.c5a)

    def mark(self, day, status):
        services.save_register(self.c5a, day, {self.anna.pk: (status, "")}, self.teacher, today=TODAY)

    def test_absence_on_a_parent_teacher_day_does_not_start_a_case(self):
        self.mark(D(2026, 1, 19), "absent")   # parent-teacher conference
        self.mark(D(2026, 1, 20), "absent")
        self.assertFalse(AbsenceCase.objects.exists())

    def test_it_neither_breaks_nor_extends_a_run(self):
        self.mark(D(2026, 1, 16), "absent")   # Friday
        self.mark(D(2026, 1, 19), "present")  # parent-teacher conference
        self.mark(D(2026, 1, 20), "absent")
        self.assertEqual(AbsenceCase.objects.get().days_absent, 2)

    def test_register_shows_the_notice(self):
        self.client.force_login(self.teacher)
        response = self.client.get(f"/attendance/class/{self.c5a.pk}/?date=2026-01-19")
        self.assertContains(response, "absences today will not start an absence case")


class ReUploadAndErrors(CalendarUploadTestCase):
    def test_re_upload_replaces_uploads_and_keeps_hand_added_items(self):
        self.upload_sample()
        manual = services.add_event(self.admin, kind="closure", name="Burst pipe", start_date=D(2026, 2, 3),
                                    end_date=D(2026, 2, 3), reason="No water")
        fh, name = csv_file(("Term", "Term 1", "2026-01-13", "2026-04-10", "", ""),
                            ("Public holiday", "Youth Day", "2026-03-12", "", "", ""))
        preview = calendar_import.parse(fh, name, self.year)
        self.assertEqual(preview.term_changes[0][1], (D(2026, 1, 12), D(2026, 4, 10)))
        created, replaced = calendar_import.apply(preview, self.admin)
        self.assertEqual((created, replaced), (1, 44))
        in_force = CalendarEvent.objects.filter(academic_year=self.year, superseded_at__isnull=True)
        self.assertEqual(set(in_force.values_list("name", flat=True)), {"Youth Day", "Burst pipe"})
        manual.refresh_from_db()
        self.assertIsNone(manual.superseded_at)
        self.assertEqual(CalendarEvent.objects.filter(superseded_at__isnull=False).count(), 44)  # kept for history
        self.assertEqual(Term.objects.get(academic_year=self.year, number=1).start_date, D(2026, 1, 13))

    def test_every_problem_is_reported_by_line(self):
        f.make_class("5")
        fh, name = csv_file(
            ("Holday", "Typo", "2026-03-01", "", "", ""),
            ("Public holiday", "Bad date", "31-31-2026", "", "", ""),
            ("Public holiday", "Last year", "2025-12-25", "", "", ""),
            ("Public holiday", "Scoped", "2026-03-12", "", "Form 4", ""),
            ("No learners", "Unknown class", "2026-03-12", "", "9Z", ""),
            ("Term", "Term 1", "2026-01-12", "2026-04-10", "", ""),
            ("Term", "Term 2", "2026-04-01", "2026-08-07", "", ""),
            ("Term", "Spring", "2026-09-07", "2026-12-04", "", ""),
        )
        preview = calendar_import.parse(fh, name, self.year)
        problems = {r.line: " ".join(r.errors) for r in preview.errors}
        self.assertIn("unknown type", problems[2])
        self.assertIn("not a date", problems[3])
        self.assertIn("not inside academic year", problems[4])
        self.assertIn("only 'No learners'", problems[5])
        self.assertIn("not a grade", problems[6])
        self.assertIn("overlaps Term 1", problems[8])
        self.assertIn("Term 1, Term 2 or Term 3", problems[9])

    def test_grade_closure_from_the_file(self):
        fh, name = csv_file(("Term", "Term 3", "2026-09-07", "2026-12-04", "", ""),
                            ("No learners", "Form 4 finished exams", "2026-12-01", "2026-12-04", "Form 4", ""))
        calendar_import.apply(calendar_import.parse(fh, name, self.year), self.admin)
        f4, f3 = f.make_class("F4"), f.make_class("F3")
        cal = for_days(D(2026, 12, 1), D(2026, 12, 4))
        self.assertFalse(cal.is_register_day(D(2026, 12, 2), f4))
        self.assertTrue(cal.is_register_day(D(2026, 12, 2), f3))

    def test_automatic_holidays_skip_an_uploaded_year(self):
        self.upload_sample()
        out = io.StringIO()
        call_command("seed_holidays", stdout=out)
        self.assertIn("has an uploaded calendar", out.getvalue())


class Screens(CalendarUploadTestCase):
    def test_template_upload_preview_and_confirm(self):
        self.client.force_login(self.admin)
        template = self.client.get("/attendance/calendar/template.xlsx")
        self.assertEqual(template.status_code, 200)
        self.assertIn("spreadsheetml", template["Content-Type"])
        with open(SAMPLE, "rb") as fh:
            upload = SimpleUploadedFile(SAMPLE.name, fh.read())
        response = self.client.post("/attendance/calendar/upload/", {"year": self.year.pk, "file": upload})
        self.assertContains(response, "Confirm and save the calendar")
        self.assertContains(response, "Parent-teacher conference")
        response = self.client.post("/attendance/calendar/upload/", {"action": "confirm"})
        self.assertRedirects(response, f"/attendance/calendar/?year={self.year.pk}")
        self.assertContains(self.client.get(response.url), "uploaded")

    def test_headteacher_cannot_upload(self):
        self.client.force_login(f.make_user("head", roles.HEADTEACHER))
        self.assertEqual(self.client.get("/attendance/calendar/upload/").status_code, 403)
