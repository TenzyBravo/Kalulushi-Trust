"""The rules the design states as database rules hold even when application code is bypassed."""
import datetime

from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import CoverAuthorization
from audit.models import AuditLog
from learners.models import Enrollment, GuardianContact, LearnerGuardian
from school.models import AcademicYear, SchoolClass, Term

from . import factories as f


class SchoolStructureRules(TestCase):
    def test_only_one_current_year(self):
        f.year("2026", status="current", start=datetime.date(2026, 1, 1))
        with self.assertRaises(IntegrityError):
            f.year("2027", status="current", start=datetime.date(2027, 1, 1))

    def test_years_cannot_overlap(self):
        f.year("2026", status="planned", start=datetime.date(2026, 1, 1))
        with self.assertRaises(IntegrityError):
            AcademicYear.objects.create(
                school=f.school(), name="2026b", start_date=datetime.date(2026, 6, 1),
                end_date=datetime.date(2027, 5, 31),
            )

    def test_terms_cannot_overlap_within_a_year(self):
        y = f.year("2026", start=datetime.date(2026, 1, 1))
        Term.objects.create(academic_year=y, number=1, start_date=datetime.date(2026, 1, 10), end_date=datetime.date(2026, 4, 10))
        with self.assertRaises(IntegrityError):
            Term.objects.create(academic_year=y, number=2, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2026, 8, 1))

    def test_class_name_is_grade_code_plus_stream(self):
        c = f.school_class("F2", "b")
        self.assertEqual(c.name, "F2B")

    def test_same_class_twice_in_one_year_refused(self):
        f.school_class("5", "A")
        with self.assertRaises(IntegrityError):
            f.school_class("5", "A")


class EnrollmentRules(TestCase):
    def setUp(self):
        self.class_5a = f.school_class("5", "A")
        self.learner = f.learner(school_class_obj=self.class_5a)
        self.enrollment = self.learner.enrollments.get()

    def _raw_enrollment(self, school_class, start, academic_year=None):
        return Enrollment.objects.create(
            school=f.school(), learner=self.learner, academic_year=academic_year or school_class.academic_year,
            school_class=school_class, start_date=start, created_by=self.enrollment.created_by,
        )

    def test_overlapping_enrollments_refused_by_database(self):
        with self.assertRaises(IntegrityError):
            self._raw_enrollment(f.school_class("5", "B"), self.enrollment.start_date)

    def test_reversed_enrollment_does_not_block(self):
        Enrollment.objects.filter(pk=self.enrollment.pk).update(status="reversed")
        self._raw_enrollment(f.school_class("5", "B"), self.enrollment.start_date)

    def test_class_must_belong_to_the_enrollments_year(self):
        other_year = f.year("1999", status="closed", start=datetime.date(1999, 1, 1))
        old_class = f.school_class("5", "A", academic_year=other_year)
        Enrollment.objects.filter(pk=self.enrollment.pk).update(status="reversed")
        with self.assertRaises(IntegrityError):
            self._raw_enrollment(old_class, self.enrollment.start_date, academic_year=self.class_5a.academic_year)

    def test_ended_enrollment_needs_reason_and_date(self):
        with self.assertRaises(IntegrityError):
            Enrollment.objects.filter(pk=self.enrollment.pk).update(status="ended")


class GuardianRules(TestCase):
    def setUp(self):
        self.learner = f.learner()
        self.link = self.learner.guardian_links.get()

    def test_phone_format_checked_by_database(self):
        with self.assertRaises(IntegrityError):
            GuardianContact.objects.filter(guardian=self.link.guardian).update(phone="0971234567")

    def test_one_current_number_per_guardian(self):
        with self.assertRaises(IntegrityError):
            GuardianContact.objects.create(guardian=self.link.guardian, phone="+260961112233")

    def test_one_primary_contact_per_learner(self):
        other = f.learner().guardian_links.get().guardian
        with self.assertRaises(IntegrityError):
            LearnerGuardian.objects.create(learner=self.learner, guardian=other, relationship="Aunt", contact_role="primary")


class AssignmentRules(TestCase):
    def setUp(self):
        self.admin = f.admin()
        self.cs = f.class_subject(f.school_class())

    def test_one_primary_teacher_at_a_time(self):
        f.assign(f.teacher(), self.cs, self.admin)
        with self.assertRaises(IntegrityError):
            f.assign(f.teacher(), self.cs, self.admin)

    def test_backup_alongside_primary_allowed(self):
        f.assign(f.teacher(), self.cs, self.admin)
        f.assign(f.teacher(), self.cs, self.admin, assignment_type="backup")

    def test_ended_primary_does_not_block_a_new_one(self):
        old = f.assign(f.teacher(), self.cs, self.admin)
        old.status = "ended"
        old.save()
        f.assign(f.teacher(), self.cs, self.admin)

    def test_cover_duties_must_be_known_and_non_empty(self):
        assignment = f.assign(f.teacher(), self.cs, self.admin)
        for duties in ([], ["marking"]):
            with self.subTest(duties=duties), self.assertRaises(IntegrityError), transaction.atomic():
                CoverAuthorization.objects.create(
                    teacher_assignment=assignment, cover_teacher=f.teacher(), starts_at=timezone.now(),
                    ends_at=timezone.now() + datetime.timedelta(hours=1), duties=duties, reason="x", authorized_by=self.admin,
                )


class AuditLogIsAppendOnly(TestCase):
    def setUp(self):
        f.learner()
        self.entry = AuditLog.objects.first()

    def test_update_refused_by_database(self):
        with self.assertRaisesMessage(DatabaseError, "append-only"):
            AuditLog.objects.filter(pk=self.entry.pk).update(reason="tampered")

    def test_delete_refused_by_database(self):
        with self.assertRaisesMessage(DatabaseError, "append-only"):
            AuditLog.objects.filter(pk=self.entry.pk).delete()

    def test_model_refuses_save_and_delete(self):
        with self.assertRaises(RuntimeError):
            self.entry.save()
        with self.assertRaises(RuntimeError):
            self.entry.delete()


class NoDeletePermissions(TestCase):
    def test_no_role_can_delete_anything(self):
        from django.contrib.auth.models import Group

        for group in Group.objects.all():
            self.assertFalse(group.permissions.filter(codename__startswith="delete_").exists(), group.name)

    def test_school_class_unique_id_year_target_exists(self):
        names = [c.name for c in SchoolClass._meta.constraints]
        self.assertIn("classes_unique_id_year", names)
