import datetime

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts import roles
from core.models import AuditLog
from learners import services as learner_services
from staffing import access, services

from . import factories as f

D = datetime.date


class TeacherAccessTests(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        f.year()
        self.c4a, self.c4b = f.make_class("4"), f.make_class("4", "B")
        maths = f.subject("4")
        self.cs_a, self.cs_b = f.class_subject(self.c4a, maths), f.class_subject(self.c4b, maths)
        self.pupil_a = f.register(self.admin, self.c4a)
        self.pupil_b = f.register(self.admin, self.c4b, first="Natasha", last="Banda", phone="0961112233",
                                  guardian="Peter Banda", dob=D(2016, 5, 1))
        self.teacher = f.make_user("t1", roles.TEACHER)
        self.backup = f.make_user("t2", roles.TEACHER)
        self.primary = services.assign_teacher(self.cs_a, self.teacher, "primary", D(2026, 1, 5), self.admin)

    def test_teacher_sees_only_assigned_class(self):
        self.assertEqual(list(access.visible_learners(self.teacher)), [self.pupil_a])

    def test_subject_in_one_class_grants_nothing_in_another(self):
        self.assertFalse(access.can_view_learner(self.teacher, self.pupil_b))

    def test_school_wide_roles_see_everyone(self):
        head = f.make_user("head", roles.HEADTEACHER)
        self.assertEqual(access.visible_learners(head).count(), 2)

    def test_backup_assignment_grants_nothing_by_itself(self):
        services.assign_teacher(self.cs_a, self.backup, "backup", D(2026, 1, 5), self.admin)
        self.assertFalse(access.visible_learners(self.backup).exists())

    def test_cover_grants_access_only_in_its_window_and_duties(self):
        services.assign_teacher(self.cs_a, self.backup, "backup", D(2026, 1, 5), self.admin)
        now = timezone.now()
        cover = services.authorize_cover(self.primary, self.backup, now - datetime.timedelta(hours=1),
                                         now + datetime.timedelta(hours=3), ["attendance"], "Primary teacher at a workshop", self.admin)
        self.assertEqual(list(access.visible_learners(self.backup)), [self.pupil_a])
        self.assertIn(self.cs_a.pk, access.class_subject_ids(self.backup, duty="attendance"))
        self.assertNotIn(self.cs_a.pk, access.class_subject_ids(self.backup, duty="assessment_entry"))
        later = now + datetime.timedelta(hours=4)
        self.assertNotIn(self.cs_a.pk, access.class_subject_ids(self.backup, at=later))  # expired by itself
        services.revoke_cover(cover, self.admin)
        self.assertFalse(access.visible_learners(self.backup).exists())

    def test_cover_only_for_named_backup(self):
        now = timezone.now()
        with self.assertRaises(ValidationError):
            services.authorize_cover(self.primary, self.backup, now, now + datetime.timedelta(hours=1),
                                     ["attendance"], "x", self.admin)

    def test_class_teacher_sees_their_class(self):
        services.assign_class_teacher(self.c4b, self.backup, D(2026, 1, 5), self.admin)
        self.assertEqual(list(access.visible_learners(self.backup)), [self.pupil_b])

    def test_support_notes_need_permission_and_class(self):
        learner_services.add_support_note(self.pupil_a, "medical", "Asthma; inhaler in bag", self.admin)
        self.assertFalse(access.can_view_support_notes(self.teacher, self.pupil_a))
        self.teacher.user_permissions.add(Permission.objects.get(codename="view_support_notes"))
        self.teacher = type(self.teacher).objects.get(pk=self.teacher.pk)  # clear the permission cache
        self.assertTrue(access.can_view_support_notes(self.teacher, self.pupil_a))
        self.assertFalse(access.can_view_support_notes(self.teacher, self.pupil_b))
        academic_admin = f.make_user("aa", roles.ACADEMIC_ADMIN)
        self.assertFalse(access.can_view_support_notes(academic_admin, self.pupil_a))

    def test_assignment_changes_are_audited(self):
        self.assertTrue(AuditLog.objects.filter(action="teacher_assignment.create", object_id=self.primary.pk).exists())
