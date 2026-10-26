import datetime

from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from accounts import roles, services
from accounts.models import User, UserSession
from core.models import AuditLog
from staffing import services as staffing
from staffing.models import CoverAuthorization, TeacherAssignment

from . import factories as f


class RoleTests(TestCase):
    def test_four_role_groups_exist(self):
        self.assertEqual(set(Group.objects.values_list("name", flat=True)), set(roles.ALL_ROLES))

    def test_no_role_can_delete_protected_records(self):
        protected = ["learner", "guardian", "enrollment", "user", "auditlog", "learnerguardian", "guardiancontact"]
        deletes = Permission.objects.filter(group__isnull=False, codename__in=[f"delete_{m}" for m in protected])
        self.assertFalse(deletes.exists())

    def test_headteacher_limits_d6(self):
        head = f.make_user("head", roles.HEADTEACHER)
        self.assertEqual(set(roles.assignable_roles(head)), {roles.TEACHER, roles.ACADEMIC_ADMIN})
        with self.assertRaises(PermissionDenied):
            services.create_staff(head, [roles.HEADTEACHER], f.PASSWORD, staff_number="H2", username="head2",
                                  email="", title="", first_name="A", last_name="B", phone="")
        teacher = services.create_staff(head, [roles.TEACHER], f.PASSWORD, staff_number="T9", username="t9",
                                        email="", title="", first_name="A", last_name="B", phone="")
        self.assertEqual(teacher.role_names, [roles.TEACHER])
        admin = f.admin_user()
        self.assertFalse(roles.can_manage(head, admin))
        self.assertTrue(roles.can_manage(head, teacher))

    def test_role_change_is_audited(self):
        admin = f.admin_user()
        t = f.make_user("t", roles.TEACHER)
        services.update_staff(admin, t, [roles.TEACHER, roles.ACADEMIC_ADMIN])
        log = AuditLog.objects.get(action="user.roles_change")
        self.assertEqual(log.changes["roles"], [[roles.TEACHER], sorted([roles.TEACHER, roles.ACADEMIC_ADMIN])])

    def test_superuser_is_system_admin(self):
        su = User.objects.create_superuser("root", "root@example.com", f.PASSWORD)
        self.assertIn(roles.SYSTEM_ADMIN, su.role_names)
        self.assertTrue(su.staff_number.startswith("SA-"))


class SignInTests(TestCase):
    def setUp(self):
        self.user = f.make_user("grace", roles.TEACHER, staff_number="KLT-T-014", email="grace@example.com")

    def test_sign_in_with_username_staff_number_or_email(self):
        for identifier in ["grace", "GRACE", "klt-t-014", "Grace@Example.com"]:
            self.assertTrue(self.client.login(username=identifier, password=f.PASSWORD), identifier)
            self.client.logout()
        self.assertFalse(self.client.login(username="grace", password="wrong"))

    def test_login_is_tracked_as_a_session(self):
        self.client.post("/login/", {"username": "grace", "password": f.PASSWORD})
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)


class DeactivationTests(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        f.year()
        self.teacher = f.make_user("t1", roles.TEACHER)
        self.backup = f.make_user("t2", roles.TEACHER)
        cg = f.make_class("4")
        self.cs = f.class_subject(cg, f.subject("4"))
        today = timezone.localdate()
        self.primary = staffing.assign_teacher(self.cs, self.teacher, "primary", datetime.date(2026, 1, 5), self.admin)
        staffing.assign_teacher(self.cs, self.backup, "backup", datetime.date(2026, 1, 5), self.admin)
        staffing.assign_class_teacher(cg, self.teacher, datetime.date(2026, 1, 5), self.admin)
        now = timezone.now()
        self.cover = staffing.authorize_cover(self.primary, self.backup, now, now + datetime.timedelta(days=2),
                                              ["attendance"], "Sick leave", self.admin)
        self.today = today

    def test_deactivation_revokes_everything_at_once(self):
        self.client.login(username="t1", password=f.PASSWORD)
        self.assertEqual(self.client.get("/").status_code, 200)
        services.deactivate(self.admin, self.teacher, "Left the school")
        # Next request from the teacher's browser is refused.
        self.assertRedirects(self.client.get("/"), "/login/?next=/")
        self.assertFalse(UserSession.objects.filter(user=self.teacher).exists())
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.is_active)
        self.assertEqual(self.teacher.deactivated_by, self.admin)
        self.assertFalse(TeacherAssignment.objects.filter(teacher=self.teacher, status="active").exists())
        self.assertIsNotNone(self.teacher.class_teacher_assignments.get().end_date)
        self.cover.refresh_from_db()
        self.assertIsNotNone(self.cover.revoked_at)
        log = AuditLog.objects.get(action="user.deactivate")
        self.assertEqual(log.reason, "Left the school")
        self.assertEqual(log.changes["sessions_ended"], [None, 1])

    def test_admin_can_end_one_session(self):
        self.client.login(username="t1", password=f.PASSWORD)
        session = UserSession.objects.get(user=self.teacher)
        services.revoke_session(self.admin, session)
        self.assertFalse(Session.objects.filter(session_key=session.session_key).exists())
        self.assertRedirects(self.client.get("/"), "/login/?next=/")


class OwnPasswordTests(TestCase):
    def test_changing_your_password_keeps_you_signed_in(self):
        f.make_user("grace", roles.TEACHER)
        self.client.login(username="grace", password=f.PASSWORD)
        response = self.client.post("/password/", {"old_password": f.PASSWORD, "new_password1": "Another-pass-2027",
                                                   "new_password2": "Another-pass-2027"})
        self.assertRedirects(response, "/")
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(UserSession.objects.get().session_key, self.client.session.session_key)
