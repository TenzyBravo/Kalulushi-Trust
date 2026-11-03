"""Slice 3: grading schemes, assessments, marks and competencies (docs/database-design-assessment.md)."""
import datetime
import json
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts import roles
from assessment import calc, services
from assessment.models import (
    AssessmentComponent, AssessmentPlan, AssessmentResult, AssessmentResultHistory, AssessmentType, Competency,
    CompetencyFramework, CompetencyLevel, GradeBand, GradingScheme,
)
from core.models import AuditLog, SchoolSettings
from school.models import Term
from staffing import services as staffing

from . import factories as f

D = datetime.date
TODAY = D(2026, 3, 11)
_real_localdate = timezone.localdate


def fake_localdate(value=None, timezone=None):
    return TODAY if value is None else _real_localdate(value, timezone)


class AssessmentTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", new=fake_localdate)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = f.admin_user()
        self.year = f.year()
        self.term = Term.objects.create(academic_year=self.year, number=1, start_date=D(2026, 1, 12), end_date=D(2026, 4, 10))
        self.c5a = f.make_class("5")
        self.maths = f.class_subject(self.c5a, f.subject("5", "Mathematics", "MATH"))
        self.english = f.class_subject(self.c5a, f.subject("5", "English", "ENG"))
        self.teacher = f.make_user("maths", roles.TEACHER)
        self.class_teacher = f.make_user("classteacher", roles.TEACHER)
        self.head = f.make_user("head", roles.HEADTEACHER)
        staffing.assign_teacher(self.maths, self.teacher, "primary", D(2026, 1, 5), self.admin)
        staffing.assign_teacher(self.english, self.class_teacher, "primary", D(2026, 1, 5), self.admin)
        staffing.assign_class_teacher(self.c5a, self.class_teacher, D(2026, 1, 12), self.admin)
        self.anna = f.register(self.admin, self.c5a)
        self.bwalya = f.register(self.admin, self.c5a, first="Bwalya", last="Banda", phone="0961112233",
                                 guardian="Peter Banda", dob=D(2016, 1, 9))
        self.scheme = GradingScheme.objects.get(stage=self.c5a.grade.stage, name="Percentage grades")
        self.plan = AssessmentPlan.objects.create(school=f.school(), academic_year=self.year, term=self.term,
                                                  stage=self.c5a.grade.stage, grading_scheme=self.scheme)
        self.ca = AssessmentComponent.objects.create(plan=self.plan, name="Continuous assessment", weight=40, sequence=1)
        self.exam = AssessmentComponent.objects.create(plan=self.plan, name="End-of-term exam", weight=60, sequence=2)
        self.test_type = AssessmentType.objects.get(name="Test")

    def assess(self, title, component, max_score, class_subject=None, actor=None):
        return services.create_assessment(
            actor or self.teacher, class_subject=class_subject or self.maths, term=self.term, component=component,
            assessment_type=self.test_type, title=title, max_score=Decimal(max_score), assessed_on=D(2026, 3, 2),
        )

    def mark(self, assessment, marks, actor=None, submit=True):
        entries = {learner.pk: (Decimal(v) if isinstance(v, (int, str)) and str(v).replace(".", "").isdigit() else None,
                                v if isinstance(v, str) and not v.replace(".", "").isdigit() else "", "")
                   for learner, v in marks.items()}
        self.assertEqual(services.save_draft(actor or self.teacher, assessment, entries), {})
        if submit:
            services.submit(actor or self.teacher, assessment)
        return assessment


class DatabaseRules(AssessmentTestCase):
    def test_score_cannot_exceed_maximum(self):
        a = self.assess("Test 1", self.ca, 25)
        enrollment = self.anna.enrollments.get()
        with self.assertRaises(IntegrityError):
            AssessmentResult.objects.create(assessment=a, class_subject=self.maths, class_group=self.c5a,
                                            enrollment=enrollment, learner=self.anna, score=Decimal(30), updated_by=self.teacher)

    def test_mark_and_missing_status_never_both(self):
        a = self.assess("Test 1", self.ca, 25)
        with self.assertRaises(IntegrityError):
            AssessmentResult.objects.create(assessment=a, class_subject=self.maths, class_group=self.c5a,
                                            enrollment=self.anna.enrollments.get(), learner=self.anna, score=Decimal(10),
                                            missing_status="absent", updated_by=self.teacher)

    def test_result_must_match_the_class_subject_class(self):
        other_class = f.make_class("5", "B")
        a = self.assess("Test 1", self.ca, 25)
        with self.assertRaises(IntegrityError):
            AssessmentResult.objects.create(assessment=a, class_subject=self.maths, class_group=other_class,
                                            enrollment=self.anna.enrollments.get(), learner=self.anna, score=Decimal(10),
                                            updated_by=self.teacher)

    def test_history_is_append_only(self):
        a = self.mark(self.assess("Test 1", self.ca, 25), {self.anna: 20, self.bwalya: 15})
        services.correct(self.head, a.results.get(learner=self.anna), score=Decimal(21), missing_status="",
                         comment="", reason="Added up wrongly")
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            AssessmentResultHistory.objects.update(reason="rewritten")

    def test_active_scheme_bands_are_frozen(self):
        band = self.scheme.bands.first()
        with self.assertRaisesMessage(DatabaseError, "new version"), transaction.atomic():
            GradeBand.objects.filter(pk=band.pk).update(label="Changed")

    def test_bands_cannot_overlap(self):
        draft = GradingScheme.objects.create(school=f.school(), stage=self.c5a.grade.stage, name="Draft", version=1)
        GradeBand.objects.create(scheme=draft, label="A", min_percent=50, max_percent=100)
        with self.assertRaises(IntegrityError):
            GradeBand.objects.create(scheme=draft, label="B", min_percent=0, max_percent=60)

    def test_one_plan_per_stage_per_term(self):
        with self.assertRaises(IntegrityError):
            AssessmentPlan.objects.create(school=f.school(), academic_year=self.year, term=self.term,
                                          stage=self.c5a.grade.stage, grading_scheme=self.scheme)


class SchemesAndPlans(AssessmentTestCase):
    def test_default_scheme_matches_b1(self):
        labels = [(b.label, b.min_percent) for b in self.scheme.bands.order_by("-min_percent")]
        self.assertEqual([l for l, _ in labels], ["Distinction", "Merit", "Credit", "Pass", "Fail"])
        self.assertEqual(self.scheme.band_for(Decimal(74)).label, "Merit")
        self.assertEqual(self.scheme.band_for(Decimal(75)).label, "Distinction")

    def test_activation_checks_coverage_and_new_version_copies_bands(self):
        copy = services.new_scheme_version(self.scheme, self.admin)
        self.assertEqual((copy.version, copy.status, copy.bands.count()), (2, "draft", 5))
        copy.bands.filter(label="Pass").delete()
        with self.assertRaisesMessage(ValidationError, "No band covers"):
            services.activate_scheme(copy, self.admin)

    def test_plan_needs_weights_adding_to_100(self):
        self.exam.weight = None
        self.exam.save()
        with self.assertRaisesMessage(ValidationError, "weight"):
            services.activate_plan(self.plan, self.admin)
        self.exam.weight = 50
        self.exam.save()
        with self.assertRaisesMessage(ValidationError, "90"):
            services.activate_plan(self.plan, self.admin)
        self.exam.weight = 60
        self.exam.save()
        services.activate_plan(self.plan, self.admin)
        self.assertTrue(AuditLog.objects.filter(action="assessment_plan.activate").exists())


class MarksAndCalculation(AssessmentTestCase):
    def test_only_the_subject_teacher_creates_assessments(self):
        with self.assertRaises(PermissionDenied):
            self.assess("Test 1", self.ca, 25, actor=self.class_teacher)

    def test_submit_needs_every_learner(self):
        a = self.assess("Test 1", self.ca, 25)
        self.mark(a, {self.anna: 20}, submit=False)
        with self.assertRaisesMessage(ValidationError, "Bwalya Banda"):
            services.submit(self.teacher, a)
        self.mark(a, {self.bwalya: "absent"})
        self.assertEqual(a.results.count(), 2)

    def test_marks_above_maximum_refused_on_save(self):
        a = self.assess("Test 1", self.ca, 25)
        errors = services.save_draft(self.teacher, a, {self.anna.pk: (Decimal(30), "", "")})
        self.assertIn("between 0 and 25", errors[self.anna.pk])

    def test_weighted_subject_score_and_grade(self):
        services.activate_plan(self.plan, self.admin)
        self.mark(self.assess("Test 1", self.ca, 25), {self.anna: 20, self.bwalya: 10})   # 80%
        self.mark(self.assess("Test 2", self.ca, 50), {self.anna: 30, self.bwalya: 20})   # 60% -> CA average 70 (B3)
        self.mark(self.assess("Exam", self.exam, 60), {self.anna: 45, self.bwalya: 30})   # 75%
        score = calc.subject_score(self.anna, self.maths, self.term)
        self.assertEqual(score.percent, Decimal("73"))  # 0.4 x 70 + 0.6 x 75
        self.assertEqual(score.band.label, "Merit")

    def test_excused_left_out_absent_blocks(self):
        services.activate_plan(self.plan, self.admin)
        self.mark(self.assess("Test 1", self.ca, 20), {self.anna: 16, self.bwalya: 10})
        self.mark(self.assess("Test 2", self.ca, 20), {self.anna: "excused", self.bwalya: 10})
        self.mark(self.assess("Exam", self.exam, 100), {self.anna: 70, self.bwalya: "absent"})
        anna = calc.subject_score(self.anna, self.maths, self.term)
        self.assertEqual(anna.percent, Decimal("74"))  # CA 80 (Test 2 left out), exam 70: 32 + 42
        bwalya = calc.subject_score(self.bwalya, self.maths, self.term)
        self.assertIsNone(bwalya.percent)
        self.assertIn("absent", bwalya.pending)

    def test_drafts_and_inactive_plans_leave_scores_pending(self):
        self.mark(self.assess("Test 1", self.ca, 20), {self.anna: 16, self.bwalya: 10})
        self.mark(self.assess("Exam", self.exam, 100), {self.anna: 70, self.bwalya: 50})
        self.assertIn("weights not set", calc.subject_score(self.anna, self.maths, self.term).pending)

    def test_correction_needs_reason_and_keeps_history(self):
        a = self.mark(self.assess("Test 1", self.ca, 25), {self.anna: 20, self.bwalya: 15})
        result = a.results.get(learner=self.anna)
        with self.assertRaisesMessage(ValidationError, "reason"):
            services.correct(self.teacher, result, score=Decimal(22), missing_status="", comment="", reason=" ")
        services.correct(self.teacher, result, score=Decimal(22), missing_status="", comment="", reason="Missed a page")
        history = result.history.get()
        self.assertEqual((history.old_score, history.new_score), (Decimal(20), Decimal(22)))
        self.assertTrue(AuditLog.objects.filter(action="assessment_result.correct").exists())
        with self.assertRaises(PermissionDenied):
            services.correct(self.class_teacher, result, score=Decimal(23), missing_status="", comment="", reason="x")

    def test_overall_score_and_competition_ranking(self):
        services.activate_plan(self.plan, self.admin)
        for cs, anna, bwalya in [(self.maths, 80, 60), (self.english, 60, 60)]:
            actor = self.teacher if cs == self.maths else self.class_teacher
            self.mark(self.assess("CA", self.ca, 100, class_subject=cs, actor=actor), {self.anna: anna, self.bwalya: bwalya}, actor=actor)
            self.mark(self.assess("Exam", self.exam, 100, class_subject=cs, actor=actor), {self.anna: anna, self.bwalya: bwalya}, actor=actor)
        _, rows = calc.class_term_results(self.c5a, self.term, [self.anna, self.bwalya])
        overall = {r.learner: r.overall for r in rows}
        self.assertEqual(overall, {self.anna: Decimal(70), self.bwalya: Decimal(60)})
        self.assertEqual(calc.competition_ranks([("a", 90), ("b", 80), ("c", 80), ("d", 70), ("e", None)]),
                         {"a": 1, "b": 2, "c": 2, "d": 4})


class Competencies(AssessmentTestCase):
    def setUp(self):
        super().setUp()
        self.framework = CompetencyFramework.objects.create(school=f.school(), stage=self.c5a.grade.stage, name="Core", version=1)
        self.critical = Competency.objects.create(framework=self.framework, code="CT", name="Critical thinking", sequence=1)
        self.comm = Competency.objects.create(framework=self.framework, code="CM", name="Communication", sequence=2)
        self.levels = [CompetencyLevel.objects.create(framework=self.framework, code=str(i), label=l, sequence=i)
                       for i, l in enumerate(["Below", "Approaching", "Meeting", "Exceeding"], start=1)]
        services.activate_framework(self.framework, self.admin)

    def test_class_teacher_records_and_submits(self):
        sheet = services.sheet_for(self.c5a, self.term, create=True, actor=self.class_teacher)
        entries = {(self.anna.pk, self.critical.pk): (self.levels[2].pk, False, ""),
                   (self.anna.pk, self.comm.pk): (None, True, ""),
                   (self.bwalya.pk, self.critical.pk): (self.levels[3].pk, False, "")}
        services.save_competencies(self.class_teacher, sheet, entries)
        with self.assertRaisesMessage(ValidationError, "Bwalya Banda"):
            services.submit_competencies(self.class_teacher, sheet)
        services.save_competencies(self.class_teacher, sheet, {(self.bwalya.pk, self.comm.pk): (self.levels[1].pk, False, "")})
        services.submit_competencies(self.class_teacher, sheet)
        result = sheet.results.get(learner=self.anna, competency=self.comm)
        self.assertTrue(result.not_assessed)
        self.assertIsNone(result.level)  # Not Assessed is never the lowest level

    def test_only_the_class_teacher_records(self):
        sheet = services.sheet_for(self.c5a, self.term, create=True, actor=self.class_teacher)
        with self.assertRaises(PermissionDenied):
            services.save_competencies(self.teacher, sheet, {})

    def test_framework_frozen_once_active(self):
        with self.assertRaisesMessage(DatabaseError, "new version"), transaction.atomic():
            Competency.objects.create(framework=self.framework, code="X", name="Extra")


class Screens(AssessmentTestCase):
    def test_teacher_creates_enters_autosaves_and_submits(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get("/assessment/"), "5A · Mathematics")
        response = self.client.post(f"/assessment/subject/{self.maths.pk}/", {
            "term": self.term.pk, "title": "Test 1", "component": self.ca.pk, "assessment_type": self.test_type.pk,
            "max_score": "25", "assessed_on": "2026-03-02",
        })
        a = self.maths.assessments.get()
        self.assertRedirects(response, f"/assessment/{a.pk}/")
        page = self.client.get(f"/assessment/{a.pk}/")
        self.assertContains(page, "Submit class")
        saved = self.client.post(f"/assessment/{a.pk}/save/", json.dumps({"learner": self.anna.pk, "score": "21"}),
                                 content_type="application/json")
        self.assertEqual(saved.json(), {"ok": True})
        bad = self.client.post(f"/assessment/{a.pk}/save/", json.dumps({"learner": self.anna.pk, "score": "30"}),
                               content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        response = self.client.post(f"/assessment/{a.pk}/", {
            "action": "submit", f"score_{self.anna.pk}": "21", f"missing_{self.anna.pk}": "",
            f"score_{self.bwalya.pk}": "", f"missing_{self.bwalya.pk}": "not_yet_assessed",
        })
        self.assertRedirects(response, f"/assessment/subject/{self.maths.pk}/")
        a.refresh_from_db()
        self.assertEqual(a.status, "submitted")

    def test_pages_render(self):
        services.activate_plan(self.plan, self.admin)
        a = self.mark(self.assess("Test 1", self.ca, 25), {self.anna: 20, self.bwalya: "absent"})
        SchoolSettings.objects.filter(school=f.school()).update(rankings_enabled=True)
        self.client.force_login(self.head)
        for url in ["/assessment/", f"/assessment/subject/{self.maths.pk}/", f"/assessment/{a.pk}/",
                    f"/assessment/{a.pk}/learner/{self.anna.pk}/", f"/assessment/class/{self.c5a.pk}/",
                    f"/assessment/competencies/{self.c5a.pk}/", "/assessment/incomplete/",
                    f"/learners/{self.anna.pk}/?tab=results", f"/learners/{self.anna.pk}/?tab=competencies",
                    "/setup/assessment/gradingscheme/", "/setup/assessment/assessmentplan/", "/staff/permissions/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(f"/assessment/class/{self.c5a.pk}/"), "Pos.")

    def test_correction_through_the_learner_page(self):
        a = self.mark(self.assess("Test 1", self.ca, 25), {self.anna: 20, self.bwalya: 15})
        self.client.force_login(self.teacher)
        response = self.client.post(f"/assessment/{a.pk}/learner/{self.anna.pk}/", {
            "score": "19", "missing_status": "", "comment": "", "reason": "Re-marked question 3"})
        self.assertRedirects(response, f"/assessment/{a.pk}/")
        self.assertEqual(a.results.get(learner=self.anna).score, Decimal(19))

    def test_other_teachers_cannot_see_the_subject(self):
        stranger = f.make_user("stranger", roles.TEACHER)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(f"/assessment/subject/{self.maths.pk}/").status_code, 404)
