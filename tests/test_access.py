"""Which learners each role can see (design §5)."""
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from accounts import access
from accounts.models import User
from audit.models import AuditLog
from core.context import acting_as
from learners import services

from . import factories as f


class TeacherAccess(TestCase):
    def setUp(self):
        self.admin = f.admin()
        self.class_5a = f.school_class("5", "A")
        self.class_5b = f.school_class("5", "B")
        self.in_5a = f.learner(self.admin, self.class_5a)
        self.in_5b = f.learner(self.admin, self.class_5b)
        self.maths_5a = f.class_subject(self.class_5a)
        self.teacher = f.teacher()

    def visible(self, user=None):
        return set(access.visible_learners(user or User.objects.get(pk=self.teacher.pk)))

    def test_unassigned_teacher_sees_nobody(self):
        self.assertEqual(self.visible(), set())

    def test_primary_assignment_grants_the_class(self):
        f.assign(self.teacher, self.maths_5a, self.admin)
        self.assertEqual(self.visible(), {self.in_5a})

    def test_backup_assignment_grants_nothing(self):
        f.assign(f.teacher(), self.maths_5a, self.admin)
        f.assign(self.teacher, self.maths_5a, self.admin, assignment_type="backup")
        self.assertEqual(self.visible(), set())

    def test_ended_assignment_grants_nothing(self):
        assignment = f.assign(self.teacher, self.maths_5a, self.admin)
        assignment.status = "ended"
        assignment.save()
        self.assertEqual(self.visible(), set())

    def test_cover_in_force_grants_its_duties_only(self):
        primary = f.assign(f.teacher(), self.maths_5a, self.admin)
        f.cover(primary, self.teacher, self.admin, duties=["attendance"])
        self.assertEqual(self.visible(), {self.in_5a})
        self.assertTrue(access.can_access_class(self.teacher, self.class_5a, duty="attendance"))
        self.assertFalse(access.can_access_class(self.teacher, self.class_5a, duty="assessment_entry"))

    def test_expired_and_revoked_cover_grant_nothing(self):
        primary = f.assign(f.teacher(), self.maths_5a, self.admin)
        f.cover(primary, self.teacher, self.admin, hours_from_now=(-5, -1))
        self.assertEqual(self.visible(), set())
        live = f.cover(primary, self.teacher, self.admin)
        with acting_as(self.admin):
            from accounts.services import revoke_cover

            revoke_cover(self.admin, live)
        self.assertEqual(self.visible(), set())

    def test_class_teacher_sees_their_class(self):
        f.class_teacher(self.teacher, self.class_5b, self.admin)
        self.assertEqual(self.visible(), {self.in_5b})

    def test_learner_who_moved_out_is_no_longer_visible(self):
        f.assign(self.teacher, self.maths_5a, self.admin)
        with acting_as(self.admin):
            services.correct(self.admin, self.in_5a.enrollments.get(), self.class_5b, "Wrong stream")
        self.assertEqual(self.visible(), set())

    def test_office_roles_see_everyone(self):
        for user in (f.admin(), f.head(), f.academic_admin()):
            self.assertEqual(self.visible(user), {self.in_5a, self.in_5b})


class SupportNotes(TestCase):
    def setUp(self):
        self.admin = f.admin()
        self.class_5a = f.school_class("5", "A")
        self.learner = f.learner(self.admin, self.class_5a)
        with acting_as(self.admin):
            services.add_note(self.admin, self.learner, "medical", "Asthma; inhaler in the office.")

    def test_head_reads_and_each_view_is_audited(self):
        head = f.head()
        with acting_as(head):
            notes = services.read_notes(head, self.learner)
        self.assertEqual(len(notes), 1)
        self.assertEqual(AuditLog.objects.filter(action="support_note.view", actor=head).count(), 1)

    def test_academic_admin_cannot_read(self):
        with self.assertRaises(PermissionDenied):
            services.read_notes(f.academic_admin(), self.learner)

    def test_teacher_needs_permission_and_class(self):
        teacher = f.teacher()
        f.assign(teacher, f.class_subject(self.class_5a), self.admin)
        teacher = User.objects.get(pk=teacher.pk)
        self.assertFalse(access.can_view_support_notes(teacher, self.learner))
        teacher.user_permissions.add(Permission.objects.get(codename="view_learnersupportnote"))
        teacher = User.objects.get(pk=teacher.pk)
        self.assertTrue(access.can_view_support_notes(teacher, self.learner))

        other_teacher = f.teacher()
        other_teacher.user_permissions.add(Permission.objects.get(codename="view_learnersupportnote"))
        self.assertFalse(access.can_view_support_notes(User.objects.get(pk=other_teacher.pk), self.learner))
