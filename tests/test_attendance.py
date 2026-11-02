"""Slice 2: calendar, daily registers and absence follow-up (docs/database-design-attendance.md)."""
import datetime
import io
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts import roles
from attendance import access, services
from attendance.calendar import for_days
from attendance.models import (
    AbsenceCase, AttendanceRecord, AttendanceSession, CalendarEvent, FollowUpAction,
)
from core.models import AuditLog
from school.models import Term
from staffing import services as staffing

from . import factories as f

D = datetime.date
TODAY = D(2026, 3, 11)  # a Wednesday in Term 1
_real_localdate = timezone.localdate


def fake_localdate(value=None, timezone=None):
    return TODAY if value is None else _real_localdate(value, timezone)


class AttendanceTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", new=fake_localdate)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = f.admin_user()
        self.year = f.year()
        for number, start, end in [(1, D(2026, 1, 12), D(2026, 4, 10)), (2, D(2026, 5, 4), D(2026, 8, 7)),
                                   (3, D(2026, 9, 7), D(2026, 12, 4))]:
            Term.objects.create(academic_year=self.year, number=number, start_date=start, end_date=end)
        self.c5a, self.c5b = f.make_class("5"), f.make_class("5", "B")
        self.teacher = f.make_user("teacher", roles.TEACHER)
        self.head = f.make_user("head", roles.HEADTEACHER)
        staffing.assign_class_teacher(self.c5a, self.teacher, D(2026, 1, 12), self.admin)
        self.anna = f.register(self.admin, self.c5a)
        self.bwalya = f.register(self.admin, self.c5a, first="Bwalya", last="Banda", phone="0961112233",
                                 guardian="Peter Banda", dob=D(2016, 1, 9))

    def take(self, day, overrides=None, actor=None, reason="", class_group=None):
        class_group = class_group or self.c5a
        overrides = overrides or {}
        rows = services.register_rows(class_group, day)
        marks = {r.learner.pk: (overrides.get(r.learner, "present"), "") for r in rows}
        return services.save_register(class_group, day, marks, actor or self.teacher, reason=reason, today=TODAY)

    def absent(self, *days, learner=None):
        for day in days:
            self.take(day, {learner or self.anna: "absent"})


class DatabaseRules(AttendanceTestCase):
    def test_one_register_per_class_per_day(self):
        self.take(D(2026, 3, 10))
        with self.assertRaises(IntegrityError):
            AttendanceSession.objects.create(school=f.school(), class_group=self.c5a, academic_year=self.year,
                                             date=D(2026, 3, 10), taken_by=self.teacher, last_saved_by=self.teacher)

    def test_one_record_per_learner_per_register(self):
        session = self.take(D(2026, 3, 10))
        record = session.records.get(learner=self.anna)
        with self.assertRaises(IntegrityError):
            AttendanceRecord.objects.create(session=session, class_group=self.c5a, enrollment=record.enrollment,
                                            learner=self.anna, status="absent", recorded_by=self.teacher)

    def test_record_must_use_an_enrollment_in_the_same_class(self):
        other = f.register(self.admin, self.c5b, first="Mwila", last="Phiri", phone="0955000111",
                           guardian="Grace Phiri", dob=D(2016, 6, 6))
        session = self.take(D(2026, 3, 10))
        session.records.filter(learner=self.anna).delete()
        with self.assertRaises(IntegrityError):
            AttendanceRecord.objects.create(session=session, class_group=self.c5a, enrollment=other.enrollments.get(),
                                            learner=other, status="present", recorded_by=self.teacher)

    def test_follow_up_history_is_append_only(self):
        self.absent(D(2026, 3, 9), D(2026, 3, 10))
        action = FollowUpAction.objects.first()
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            FollowUpAction.objects.filter(pk=action.pk).update(outcome="rewritten")
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            FollowUpAction.objects.filter(pk=action.pk).delete()

    def test_one_open_case_per_learner(self):
        self.absent(D(2026, 3, 9), D(2026, 3, 10))
        with self.assertRaises(IntegrityError):
            AbsenceCase.objects.create(school=f.school(), learner=self.anna, class_group=self.c5a,
                                       first_absent_date=D(2026, 3, 2), last_absent_date=D(2026, 3, 3))


class Calendar(AttendanceTestCase):
    def event(self, kind, start, end=None, **kw):
        kw.setdefault("reason", "test" if kind == "closure" else "")
        return CalendarEvent.objects.create(school=f.school(), academic_year=self.year, kind=kind, name=kind,
                                            start_date=start, end_date=end, created_by=self.admin, **kw)

    def test_term_weekdays_are_school_days(self):
        cal = for_days(D(2026, 1, 1), D(2026, 12, 31))
        self.assertTrue(cal.is_school_day(D(2026, 3, 10)))
        self.assertFalse(cal.is_school_day(D(2026, 3, 14)))  # Saturday
        self.assertFalse(cal.is_school_day(D(2026, 4, 20)))  # between terms

    def test_holidays_closures_and_extra_days(self):
        self.event("holiday", D(2026, 3, 9), D(2026, 3, 9))
        self.event("closure", D(2026, 3, 16), D(2026, 3, 17))
        self.event("extra_school_day", D(2026, 3, 14), D(2026, 3, 14))
        cal = for_days(D(2026, 3, 1), D(2026, 3, 31))
        self.assertFalse(cal.is_school_day(D(2026, 3, 9)))
        self.assertFalse(cal.is_school_day(D(2026, 3, 17)))
        self.assertTrue(cal.is_school_day(D(2026, 3, 14)))

    def test_grade_closure_stops_only_that_grade_until_end_of_term(self):
        f4 = f.make_class("F4")
        self.event("closure", D(2026, 3, 30), None, grade=f4.grade)
        cal = for_days(D(2026, 3, 1), D(2026, 5, 31))
        self.assertTrue(cal.is_school_day(D(2026, 4, 1)))
        self.assertFalse(cal.is_register_day(D(2026, 4, 1), f4))
        self.assertTrue(cal.is_register_day(D(2026, 4, 1), self.c5a))
        self.assertTrue(cal.is_register_day(D(2026, 5, 5), f4))  # Term 2: the closure has ended

    def test_class_closure(self):
        self.event("closure", D(2026, 3, 30), None, class_group=self.c5b)
        cal = for_days(D(2026, 3, 1), D(2026, 4, 30))
        self.assertFalse(cal.is_register_day(D(2026, 3, 31), self.c5b))
        self.assertTrue(cal.is_register_day(D(2026, 3, 31), self.c5a))

    def test_database_rules_for_events(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.event("holiday", D(2026, 3, 9), None, grade=self.c5a.grade)  # holidays are school-wide
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.event("closure", D(2026, 3, 9), None)  # a school-wide closure needs an end date

    def test_closure_through_the_service_is_audited_and_head_may_close(self):
        f4 = f.make_class("F4")
        event = services.add_event(self.head, kind="closure", name="Form 4 finished exams",
                                   start_date=D(2026, 11, 20), reason="Final ECZ exams written", grade=f4.grade)
        self.assertIsNone(event.end_date)
        self.assertTrue(AuditLog.objects.filter(action="calendar_event.create").exists())
        with self.assertRaises(PermissionDenied):
            services.add_event(self.head, kind="holiday", name="X", start_date=D(2026, 3, 9))
        services.end_event(event, D(2026, 11, 25), self.head, "Recalled for a re-sit")
        event.refresh_from_db()
        self.assertEqual(event.end_date, D(2026, 11, 25))

    def test_seed_holidays(self):
        out = io.StringIO()
        call_command("seed_holidays", "--year", "2026", "--as-user", self.admin.username, stdout=out)
        self.assertIn("Added 17", out.getvalue())
        call_command("seed_holidays", "--year", "2026", "--as-user", self.admin.username, stdout=out)
        self.assertIn("Added 0", out.getvalue())
        self.assertTrue(CalendarEvent.objects.filter(name="Easter Monday", start_date=D(2026, 4, 6)).exists())
        self.assertFalse(for_days(D(2026, 3, 1), D(2026, 3, 31)).is_school_day(D(2026, 3, 12)))  # Youth Day


class Registers(AttendanceTestCase):
    def test_class_teacher_takes_the_register(self):
        session = self.take(D(2026, 3, 11), {self.bwalya: "late"})
        self.assertEqual(session.records.count(), 2)
        self.assertEqual(session.records.get(learner=self.bwalya).status, "late")
        self.assertTrue(AuditLog.objects.filter(action="attendance.save").exists())

    def test_every_learner_must_be_marked(self):
        marks = {self.anna.pk: ("present", "")}
        with self.assertRaisesMessage(ValidationError, "Bwalya Banda"):
            services.save_register(self.c5a, TODAY, marks, self.teacher, today=TODAY)

    def test_subject_teacher_cannot_take_it(self):
        subject_teacher = f.make_user("maths", roles.TEACHER)
        staffing.assign_teacher(f.class_subject(self.c5a, f.subject("5")), subject_teacher, "primary", D(2026, 1, 12), self.admin)
        with self.assertRaises(PermissionDenied):
            self.take(TODAY, actor=subject_teacher)
        self.assertEqual(access.register_access(subject_teacher, self.c5a, TODAY, False, today=TODAY), access.VIEW)

    def test_office_roles_cannot_take_but_can_correct_with_reason(self):
        with self.assertRaisesMessage(ValidationError, "reason"):
            self.take(TODAY, actor=self.head)
        self.take(TODAY, actor=self.head, reason="Class teacher off sick; register from the paper copy")
        self.assertTrue(AuditLog.objects.filter(action="attendance.correct").exists())

    def test_teacher_may_edit_within_two_school_days(self):
        self.take(D(2026, 3, 9))  # Monday: two school days before Wednesday
        self.take(D(2026, 3, 9), {self.anna: "late"})
        self.assertEqual(AttendanceRecord.objects.get(learner=self.anna).status, "late")

    def test_old_registers_are_refused_to_the_teacher(self):
        with self.assertRaises(PermissionDenied):
            self.take(D(2026, 3, 3))  # 6 school days back, beyond the 5-day back-fill window
        self.take(D(2026, 3, 4))  # exactly 5 back: allowed
        with self.assertRaises(PermissionDenied):
            self.take(D(2026, 3, 4), {self.anna: "late"})  # 5 school days old: beyond the 2-day edit window

    def test_closed_grade_refuses_registers_while_others_continue(self):
        services.add_event(self.admin, kind="closure", name="5A exams over", start_date=D(2026, 3, 9),
                           reason="Mock exams finished", class_group=self.c5a)
        with self.assertRaisesMessage(ValidationError, "closed"):
            self.take(D(2026, 3, 10))
        teacher_b = f.make_user("teacherb", roles.TEACHER)
        staffing.assign_class_teacher(self.c5b, teacher_b, D(2026, 1, 12), self.admin)
        f.register(self.admin, self.c5b, first="Mwila", last="Phiri", phone="0955000111", guardian="Grace Phiri",
                   dob=D(2016, 6, 6))
        self.take(D(2026, 3, 10), actor=teacher_b, class_group=self.c5b)

    def test_holidays_and_future_dates_refused(self):
        CalendarEvent.objects.create(school=f.school(), academic_year=self.year, kind="holiday", name="Youth Day",
                                     start_date=D(2026, 3, 10), end_date=D(2026, 3, 10), created_by=self.admin)
        with self.assertRaisesMessage(ValidationError, "Youth Day"):
            self.take(D(2026, 3, 10))
        with self.assertRaisesMessage(ValidationError, "future"):
            self.take(D(2026, 3, 12))


class AbsenceWarning(AttendanceTestCase):
    def case(self):
        return AbsenceCase.objects.filter(learner=self.anna).first()

    def test_two_consecutive_absences_open_a_case_for_the_class_teacher(self):
        self.absent(D(2026, 3, 9))
        self.assertIsNone(self.case())
        self.absent(D(2026, 3, 10))
        case = self.case()
        self.assertEqual((case.status, case.days_absent, case.assigned_teacher), ("new", 2, self.teacher))
        self.assertEqual(case.case_records.count(), 2)
        self.assertEqual(case.actions.get().kind, "system_note")

    def test_late_or_excused_breaks_the_run(self):
        self.absent(D(2026, 3, 6))
        self.take(D(2026, 3, 9), {self.anna: "late"})
        self.absent(D(2026, 3, 10))
        self.assertIsNone(self.case())

    def test_a_day_with_no_register_neither_breaks_nor_extends(self):
        self.absent(D(2026, 3, 6))
        # no register on Monday 9th
        self.absent(D(2026, 3, 10))
        self.assertEqual(self.case().days_absent, 2)

    def test_holidays_are_skipped(self):
        CalendarEvent.objects.create(school=f.school(), academic_year=self.year, kind="holiday", name="Holiday",
                                     start_date=D(2026, 3, 9), end_date=D(2026, 3, 9), created_by=self.admin)
        self.absent(D(2026, 3, 6), D(2026, 3, 10))
        self.assertIsNotNone(self.case())

    def test_further_absence_extends_the_open_case(self):
        self.absent(D(2026, 3, 9), D(2026, 3, 10), D(2026, 3, 11))
        self.assertEqual(AbsenceCase.objects.count(), 1)
        self.assertEqual(self.case().days_absent, 3)
        self.assertEqual(self.case().last_absent_date, D(2026, 3, 11))

    def test_backfilling_a_missed_day_joins_the_run(self):
        self.absent(D(2026, 3, 11))
        self.absent(D(2026, 3, 10))  # filled in afterwards
        self.assertEqual(self.case().first_absent_date, D(2026, 3, 10))

    def test_correcting_an_absence_keeps_the_case_open_with_a_note(self):
        self.absent(D(2026, 3, 10), D(2026, 3, 11))
        self.take(D(2026, 3, 11), {self.anna: "excused"})
        case = self.case()
        self.assertEqual(case.status, "new")
        self.assertIn("changed to excused", case.actions.last().outcome)

    def test_new_run_after_resolution_opens_a_new_case(self):
        self.absent(D(2026, 3, 4), D(2026, 3, 5))
        services.change_status(self.case(), "resolved", self.teacher, "Had malaria; back at school")
        self.take(D(2026, 3, 6))
        self.absent(D(2026, 3, 10), D(2026, 3, 11))
        self.assertEqual(AbsenceCase.objects.filter(learner=self.anna).count(), 2)


class CaseWorkflow(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        self.absent(D(2026, 3, 10), D(2026, 3, 11))
        self.case = AbsenceCase.objects.get()

    def test_contact_escalate_and_headteacher_resolves(self):
        services.add_contact(self.case, self.teacher, contact_date=TODAY, contact_method="phone_call",
                             outcome="Mother says she is sick", reason_category="illness")
        services.change_status(self.case, "in_progress", self.teacher)
        with self.assertRaisesMessage(ValidationError, "why"):
            services.change_status(self.case, "escalated", self.teacher)
        services.change_status(self.case, "escalated", self.teacher, "No answer for a week; family moved?")
        with self.assertRaises(PermissionDenied):
            services.change_status(self.case, "resolved", self.teacher, "x")
        services.change_status(self.case, "resolved", self.head, "Home visit: family relocated, transfer letter coming")
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, "resolved")
        kinds = list(self.case.actions.values_list("kind", flat=True))
        self.assertEqual(kinds, ["system_note", "contact", "status_change", "referral", "status_change"])

    def test_academic_admin_can_work_on_cases_other_teachers_cannot(self):
        aa = f.make_user("aa", roles.ACADEMIC_ADMIN)
        services.add_note(self.case, aa, "Checked the register with the class teacher")
        stranger = f.make_user("stranger", roles.TEACHER)
        with self.assertRaises(PermissionDenied):
            services.add_note(self.case, stranger, "x")
        self.assertNotIn(self.case, access.visible_cases(stranger))

    def test_resolved_is_final(self):
        services.change_status(self.case, "resolved", self.teacher, "Back at school")
        with self.assertRaises(ValidationError):
            services.change_status(self.case, "in_progress", self.head)


class Screens(AttendanceTestCase):
    def test_register_page_and_saving_through_it(self):
        self.client.force_login(self.teacher)
        url = f"/attendance/class/{self.c5a.pk}/?date=2026-03-11"
        response = self.client.get(url)
        self.assertContains(response, "Mark all present")
        self.assertContains(response, self.anna.learner_number)
        response = self.client.post(url, {"date": "2026-03-11", f"status_{self.anna.pk}": "absent",
                                          f"status_{self.bwalya.pk}": "present", f"note_{self.anna.pk}": "fever"})
        self.assertRedirects(response, url)
        self.assertEqual(AttendanceRecord.objects.get(learner=self.anna).note, "fever")
        response = self.client.post(url, {"date": "2026-03-11", f"status_{self.anna.pk}": "absent"})
        self.assertContains(response, "Not yet marked: Bwalya Banda")

    def test_pages_render(self):
        self.absent(D(2026, 3, 10), D(2026, 3, 11))
        case = AbsenceCase.objects.get()
        self.client.force_login(self.head)
        for url in ["/attendance/", "/attendance/?date=2026-03-14", "/attendance/follow-up/",
                    "/attendance/follow-up/?status=all", f"/attendance/follow-up/{case.pk}/",
                    f"/attendance/follow-up/{case.pk}/?form=status", "/attendance/calendar/",
                    "/attendance/calendar/?add=closure", f"/learners/{self.anna.pk}/?tab=attendance",
                    f"/learners/{self.anna.pk}/?tab=followup", "/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_headteacher_sees_registers_not_taken(self):
        self.absent(D(2026, 3, 9), D(2026, 3, 10))
        self.client.force_login(self.head)
        response = self.client.get("/attendance/")
        self.assertContains(response, "Not taken")
        response = self.client.get("/")
        self.assertContains(response, "registers not taken today")
        self.assertContains(response, "new absence cases")

    def test_teacher_dashboard_and_case_list(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get("/"), "register for 5A not taken today")
        self.absent(D(2026, 3, 10), D(2026, 3, 11))
        self.assertContains(self.client.get("/attendance/follow-up/"), self.anna.full_name)
        case = AbsenceCase.objects.get()
        response = self.client.post(f"/attendance/follow-up/{case.pk}/", {
            "action": "contact", "contact_date": "2026-03-11", "contact_method": "home_visit", "outcome": "Visited home",
        })
        self.assertRedirects(response, f"/attendance/follow-up/{case.pk}/")

    def test_closing_a_grade_from_the_calendar_screen(self):
        f4 = f.make_class("F4")
        self.client.force_login(self.head)
        response = self.client.post("/attendance/calendar/", {
            "add": "closure", "scope": "grade", "grade": f4.grade.pk, "start_date": "2026-11-20",
            "reason": "Final exams written",
        })
        self.assertEqual(response.status_code, 302)
        event = CalendarEvent.objects.get()
        self.assertEqual((event.grade, event.end_date), (f4.grade, None))

    def test_teacher_cannot_see_other_class_registers(self):
        stranger = f.make_user("stranger", roles.TEACHER)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(f"/attendance/class/{self.c5a.pk}/").status_code, 404)
