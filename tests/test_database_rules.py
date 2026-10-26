"""Rules the design puts in the database itself, checked by writing past the application."""
import datetime

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from core.models import AuditLog
from learners.models import Enrollment, GuardianContact, LearnerGuardian
from school.models import AcademicYear, Term
from staffing.models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment

from . import factories as f

D = datetime.date


class EnrollmentRules(TestCase):
    def setUp(self):
        self.actor = f.admin_user()
        self.year = f.year()
        self.c5a, self.c5b = f.make_class("5"), f.make_class("5", "B")
        self.learner = f.register(self.actor, self.c5a)

    def test_overlapping_enrollments_cannot_both_commit(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(school=self.year.school, learner=self.learner, academic_year=self.year,
                                      class_group=self.c5b, start_date=D(2026, 6, 15), created_by=self.actor)

    def test_reversed_enrollment_does_not_block(self):
        current = self.learner.current_enrollment()
        current.status = "reversed"
        current.save()
        Enrollment.objects.create(school=self.year.school, learner=self.learner, academic_year=self.year,
                                  class_group=self.c5b, start_date=D(2026, 1, 15), created_by=self.actor)

    def test_class_from_another_year_is_impossible(self):
        other_year = f.year("2027", status="planned")
        other_class = f.make_class("6", academic_year=other_year)
        Enrollment.objects.filter(learner=self.learner).update(status="reversed")  # so only the FK can object
        with self.assertRaises(IntegrityError) as ctx, transaction.atomic():
            Enrollment.objects.create(school=self.year.school, learner=self.learner, academic_year=self.year,
                                      class_group=other_class, start_date=D(2026, 12, 31), created_by=self.actor)
        self.assertIn("enrollments_class_in_year_fk", str(ctx.exception))

    def test_ended_enrollment_needs_reason(self):
        e = self.learner.current_enrollment()
        e.status, e.end_date = "ended", D(2026, 3, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            e.save()


class StructureRules(TestCase):
    def setUp(self):
        self.year = f.year()

    def test_one_current_year(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.year("2027", status="current")

    def test_years_cannot_overlap(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicYear.objects.create(school=self.year.school, name="2026b", start_date=D(2026, 12, 1),
                                        end_date=D(2027, 6, 1))

    def test_terms_cannot_overlap(self):
        Term.objects.create(academic_year=self.year, number=1, start_date=D(2026, 1, 13), end_date=D(2026, 4, 10))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Term.objects.create(academic_year=self.year, number=2, start_date=D(2026, 4, 1), end_date=D(2026, 8, 1))

    def test_seeded_grades(self):
        from school.models import Grade

        self.assertEqual([g.name for g in Grade.objects.all()][:1] + [g.name for g in Grade.objects.all()][-1:],
                         ["Grade 1", "Form 4"])
        self.assertEqual(Grade.objects.count(), 11)


class StaffingRules(TestCase):
    def setUp(self):
        self.actor = f.admin_user()
        f.year()
        self.cs = f.class_subject(f.make_class("3"), f.subject("3"))
        self.t1, self.t2 = f.make_user("t1", "Teacher"), f.make_user("t2", "Teacher")

    def _assign(self, teacher, kind="primary", start=D(2026, 1, 1)):
        return TeacherAssignment.objects.create(school=self.actor.school, teacher=teacher, class_subject=self.cs,
                                                assignment_type=kind, start_date=start, assigned_by=self.actor)

    def test_one_primary_teacher_at_a_time(self):
        self._assign(self.t1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._assign(self.t2)

    def test_backup_alongside_primary_is_fine(self):
        self._assign(self.t1)
        self._assign(self.t2, "backup")

    def test_one_class_teacher_at_a_time(self):
        cg = self.cs.class_group
        ClassTeacherAssignment.objects.create(class_group=cg, teacher=self.t1, start_date=D(2026, 1, 1), assigned_by=self.actor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ClassTeacherAssignment.objects.create(class_group=cg, teacher=self.t2, start_date=D(2026, 2, 1), assigned_by=self.actor)

    def test_cover_duties_must_be_known(self):
        primary = self._assign(self.t1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CoverAuthorization.objects.create(teacher_assignment=primary, cover_teacher=self.t2,
                                              starts_at="2026-03-01T08:00Z", ends_at="2026-03-02T08:00Z",
                                              duties=["fees"], reason="x", authorized_by=self.actor)


class GuardianRules(TestCase):
    def setUp(self):
        self.actor = f.admin_user()
        f.year()
        self.learner = f.register(self.actor, f.make_class("2"))
        self.guardian = self.learner.guardians.get()

    def test_phone_format_enforced(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuardianContact.objects.create(guardian=self.guardian, phone="0971234567", is_current=False)

    def test_one_current_number(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuardianContact.objects.create(guardian=self.guardian, phone="+260961112233")

    def test_one_primary_contact(self):
        from learners.services import create_guardian

        other = create_guardian(self.learner.school, "John Mwansa", "0961112233", "", self.actor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LearnerGuardian.objects.create(learner=self.learner, guardian=other, contact_role="primary")


class AuditRules(TestCase):
    def test_audit_log_is_append_only_in_the_database(self):
        actor = f.admin_user()
        f.year()
        f.register(actor, f.make_class("1"))
        log = AuditLog.objects.first()
        with self.assertRaises(Exception), transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE audit_logs SET action = 'x' WHERE id = %s", [log.pk])
        with self.assertRaises(Exception), transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE id = %s", [log.pk])
        with self.assertRaises(ValueError):
            log.save()
