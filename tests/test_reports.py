"""Reports and exports (docs/design-reports.md): figures from source records, access as on screen, exports logged."""
import datetime
from decimal import Decimal
from unittest import mock

from django.db import DatabaseError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts import roles
from assessment import services as assessment
from assessment.models import AssessmentComponent, AssessmentPlan, AssessmentType, GradingScheme
from attendance import services as attendance
from core.models import AuditLog
from learners.models import Learner
from reports import queries
from reports.models import ReportExport
from school.models import Term
from staffing import services as staffing

from . import factories as f

D = datetime.date
TODAY = D(2026, 3, 11)
_real_localdate = timezone.localdate


def fake_localdate(value=None, timezone=None):
    return TODAY if value is None else _real_localdate(value, timezone)


class ReportsTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", new=fake_localdate)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = f.admin_user()
        self.head = f.make_user("head", roles.HEADTEACHER)
        self.teacher = f.make_user("teacher", roles.TEACHER)
        self.maths_teacher = f.make_user("maths", roles.TEACHER)
        self.year = f.year()
        self.term = Term.objects.create(academic_year=self.year, number=1, start_date=D(2026, 1, 12), end_date=D(2026, 4, 10))
        self.c5a, self.c5b = f.make_class("5"), f.make_class("5", "B")
        staffing.assign_class_teacher(self.c5a, self.teacher, D(2026, 1, 12), self.admin)
        self.maths = f.class_subject(self.c5a, f.subject("5", "Mathematics", "MATH"))
        staffing.assign_teacher(self.maths, self.maths_teacher, "primary", D(2026, 1, 5), self.admin)
        self.anna = f.register(self.admin, self.c5a)
        self.bwalya = f.register(self.admin, self.c5a, first="Bwalya", last="Banda", phone="0961112233",
                                 guardian="Peter Banda", dob=D(2016, 1, 9))
        Learner.objects.filter(pk=self.bwalya.pk).update(gender="M")
        self.mwila = f.register(self.admin, self.c5b, first="Mwila", last="Phiri", phone="0955000111",
                                guardian="Grace Phiri", dob=D(2016, 6, 6))
        for day, anna, bwalya in [(D(2026, 3, 9), "present", "present"), (D(2026, 3, 10), "absent", "late"),
                                  (D(2026, 3, 11), "present", "excused")]:
            attendance.save_register(self.c5a, day, {self.anna.pk: (anna, ""), self.bwalya.pk: (bwalya, "")}, self.teacher, today=TODAY)

    def scope(self, user, **kw):
        return queries.build_scope(user, self.year.pk, kw.get("term", self.term.pk))


class Attendance(ReportsTestCase):
    def test_school_level_counts_and_rate(self):
        result = queries.attendance(self.scope(self.head))
        primary = next(t for t in result.tables if t.title == "Primary")
        grade5 = next(r for r in primary.rows if str(r[0]) == "Grade 5")
        # present, late, absent, excused; rate = (3 present + 1 late) / 6 records
        self.assertEqual(grade5[2:6], [3, 1, 1, 1])
        self.assertEqual(str(grade5[6]), "67%")
        self.assertEqual(grade5[7], "67%")   # girls: Anna 2 of 3
        self.assertEqual(grade5[8], "67%")   # boys: Bwalya present + late of 3
        self.assertFalse(result.sensitive)

    def test_class_level_lists_learners_and_absence_dates(self):
        result = queries.attendance(self.scope(self.head), class_id=self.c5a.pk)
        learners = result.tables[1]
        anna = next(r for r in learners.rows if str(r[0]) == self.anna.full_name)
        self.assertEqual(anna[-1], "10 Mar")
        self.assertTrue(result.sensitive)

    def test_teachers_only_see_their_classes(self):
        scope = self.scope(self.teacher)
        self.assertEqual(scope.classes, [self.c5a])
        result = queries.attendance(scope, class_id=self.c5b.pk)  # not theirs: falls back to their own classes
        self.assertEqual(result.level, "mine")
        self.assertNotIn("5B", " ".join(str(c) for t in result.tables for r in t.rows for c in r))


class Performance(ReportsTestCase):
    def test_class_subject_average_pass_rate_and_grades(self):
        scheme = GradingScheme.objects.get(stage=self.c5a.grade.stage, name="Percentage grades")
        plan = AssessmentPlan.objects.create(school=f.school(), academic_year=self.year, term=self.term,
                                             stage=self.c5a.grade.stage, grading_scheme=scheme)
        comp = AssessmentComponent.objects.create(plan=plan, name="Exam", weight=100)
        assessment.activate_plan(plan, self.admin)
        a = assessment.create_assessment(self.maths_teacher, class_subject=self.maths, term=self.term, component=comp,
                                         assessment_type=AssessmentType.objects.get(name="Test"), title="Exam",
                                         max_score=Decimal(100), assessed_on=D(2026, 3, 2))
        assessment.save_draft(self.maths_teacher, a, {self.anna.pk: (Decimal(80), "", ""), self.bwalya.pk: (Decimal(30), "", "")})
        assessment.submit(self.maths_teacher, a)
        result = queries.performance(self.scope(self.head), class_id=self.c5a.pk)
        maths = result.tables[0].rows[0]
        self.assertEqual(maths[:3], ["Mathematics", 2, 55.0])
        self.assertEqual(str(maths[3]), "50%")          # Anna passes, Bwalya fails
        self.assertEqual(maths[5:7], [80.0, 30.0])     # girls, boys
        self.assertIn("Distinction 1", maths[7])
        school = queries.performance(self.scope(self.head))
        self.assertIn("Percentage grades", str(school.tables[0].rows[0][-1]))


class EnrollmentAndContacts(ReportsTestCase):
    def test_class_list_contacts_only_for_office_and_class_teacher(self):
        for user, shown in [(self.head, True), (self.teacher, True), (self.maths_teacher, False)]:
            result = queries.enrollment(self.scope(user), class_id=self.c5a.pk)
            self.assertEqual("Mobile" in result.tables[0].columns, shown, user)

    def test_school_level_gender_counts(self):
        result = queries.enrollment(self.scope(self.head))
        primary = next(t for t in result.tables if t.title == "Primary")
        self.assertEqual(primary.total[1:], [2, 1, 0, 3])  # girls, boys, not recorded, total


class Exports(ReportsTestCase):
    def test_excel_export_is_logged_and_learner_level_is_audited(self):
        self.client.force_login(self.head)
        response = self.client.get(f"/reports/attendance/?year={self.year.pk}&term={self.term.pk}&format=xlsx")
        self.assertIn("spreadsheetml", response["Content-Type"])
        export = ReportExport.objects.get()
        self.assertFalse(export.sensitive)
        self.assertFalse(AuditLog.objects.filter(action="report.export").exists())
        self.client.get(f"/reports/attendance/?year={self.year.pk}&term={self.term.pk}&cls={self.c5a.pk}&format=xlsx")
        self.assertTrue(ReportExport.objects.filter(sensitive=True).exists())
        self.assertTrue(AuditLog.objects.filter(action="report.export").exists())

    def test_exports_log_is_append_only(self):
        self.client.force_login(self.head)
        self.client.get(f"/reports/enrollment/?year={self.year.pk}&format=print")
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            ReportExport.objects.update(row_count=0)

    def test_print_view(self):
        self.client.force_login(self.head)
        response = self.client.get(f"/reports/enrollment/?year={self.year.pk}&cls={self.c5a.pk}&format=print")
        self.assertContains(response, "Confidential")
        self.assertContains(response, "Print or save as PDF")


class LearnerReportAndPages(ReportsTestCase):
    def test_learner_report_sections_and_access(self):
        self.client.force_login(self.teacher)
        response = self.client.get(f"/reports/learner/?learner={self.anna.pk}")
        for section in ["Enrollment history", "Attendance", "Absence follow-up", "Results by subject and term",
                        "Competency development", "Overall score trend"]:
            self.assertContains(response, section)
        self.assertContains(response, "10 Mar")
        self.assertEqual(self.client.get(f"/reports/learner/?learner={self.mwila.pk}").status_code, 404)

    def test_every_report_page_renders(self):
        self.client.force_login(self.head)
        for name in ["attendance", "followup", "performance", "competencies", "enrollment"]:
            for extra in ["", f"&grade={self.c5a.grade_id}", f"&cls={self.c5a.pk}"]:
                url = f"/reports/{name}/?year={self.year.pk}{extra}"
                self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/reports/").status_code, 200)
        self.assertEqual(self.client.get("/reports/exports/").status_code, 200)
        self.assertContains(self.client.get(f"/learners/{self.anna.pk}/"), "Full report")

    def test_teachers_cannot_open_the_exports_log(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get("/reports/exports/").status_code, 403)
        self.assertEqual(self.client.get("/reports/attendance/").status_code, 200)
