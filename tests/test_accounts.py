from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts import roles, services
from accounts.models import User, UserSession
from audit.models import AuditLog
from core.context import acting_as
from school import services as school_services

from . import factories as f


class SignIn(TestCase):
    def setUp(self):
        self.user = f.teacher()
        self.user.email = "Banda@School.zm"
        self.user.staff_number = "T-042"
        self.user.save()

    def sign_in(self, identifier, password=f.PASSWORD):
        return self.client.post(reverse("login"), {"username": identifier, "password": password})

    def test_staff_number_username_or_email_any_case(self):
        for identifier in ("t-042", self.user.username.upper(), "banda@school.zm"):
            with self.subTest(identifier=identifier):
                self.client.logout()
                response = self.sign_in(identifier)
                self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_wrong_password_refused(self):
        response = self.sign_in("T-042", "wrong")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "didn&#x27;t match")

    def test_sign_in_records_a_session(self):
        self.sign_in("T-042")
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)

    def test_username_is_unique_ignoring_case(self):
        with self.assertRaises(Exception):
            f.user(roles.TEACHER, username=self.user.username.upper())


class Deactivation(TestCase):
    def setUp(self):
        self.admin = f.admin()
        self.teacher = f.teacher()
        self.class_5a = f.school_class("5", "A")
        self.cs = f.class_subject(self.class_5a)
        self.assignment = f.assign(self.teacher, self.cs, self.admin)
        self.class_teacher = f.class_teacher(self.teacher, self.class_5a, self.admin)
        other = f.assign(f.teacher(), f.class_subject(f.school_class("5", "B")), self.admin)
        self.cover = f.cover(other, self.teacher, self.admin)
        self.client.post(reverse("login"), {"username": self.teacher.username, "password": f.PASSWORD})

    def deactivate(self):
        with acting_as(self.admin):
            services.deactivate(self.admin, self.teacher, "Left the school")

    def test_everything_ends_together_and_is_audited(self):
        self.deactivate()
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.is_active)
        self.assertEqual(self.teacher.deactivated_by, self.admin)
        self.assertFalse(UserSession.objects.filter(user=self.teacher).exists())
        self.assertFalse(Session.objects.exists())
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, "ended")
        self.class_teacher.refresh_from_db()
        self.assertIsNotNone(self.class_teacher.end_date)
        self.cover.refresh_from_db()
        self.assertIsNotNone(self.cover.revoked_at)
        entry = AuditLog.objects.get(action="user.deactivate")
        self.assertEqual(entry.reason, "Left the school")
        self.assertEqual(entry.changes["covers_revoked"], 1)

    def test_signed_out_on_next_request(self):
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        self.deactivate()
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_refused_even_if_a_session_survived(self):
        # Simulate a session row the revocation missed: is_active is re-checked on every request.
        User.objects.filter(pk=self.teacher.pk).update(is_active=False)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)

    def test_reason_required(self):
        with self.assertRaises(ValidationError):
            services.deactivate(self.admin, self.teacher, "  ")


class RoleLimits(TestCase):
    """Decision D6: a Headteacher manages only Teachers and Academic Administrators."""

    def setUp(self):
        self.head = f.head()
        self.admin = f.admin()

    def create(self, actor, role):
        with acting_as(actor):
            return services.create_staff(
                actor, role_names=[role], password=f.PASSWORD, username=f"new{role[:3]}",
                staff_number=f"N-{role[:3]}", first_name="New", last_name="Person",
            )

    def test_head_creates_teacher_and_academic_admin(self):
        self.assertTrue(self.create(self.head, roles.TEACHER).has_role(roles.TEACHER))
        self.assertTrue(self.create(self.head, roles.ACADEMIC_ADMIN).is_staff)
        self.assertTrue(AuditLog.objects.filter(action="user.create", actor=self.head).exists())

    def test_head_cannot_create_head_or_system_admin(self):
        for role in (roles.HEADTEACHER, roles.SYSTEM_ADMIN):
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                self.create(self.head, role)

    def test_head_cannot_deactivate_system_admin(self):
        with self.assertRaises(PermissionDenied):
            services.deactivate(self.head, self.admin, "no")

    def test_nobody_deactivates_themselves(self):
        with self.assertRaises(PermissionDenied):
            services.deactivate(self.admin, self.admin, "oops")

    def test_academic_admin_cannot_create_staff(self):
        with self.assertRaises(PermissionDenied):
            self.create(f.academic_admin(), roles.TEACHER)

    def test_teachers_do_not_get_the_admin_site(self):
        self.assertFalse(f.teacher().is_staff)


class AcademicYears(TestCase):
    def test_making_a_year_current_closes_the_old_one(self):
        import datetime

        old = f.year("2026", start=datetime.date(2026, 1, 1))
        new = f.year("2027", status="planned", start=datetime.date(2027, 1, 1))
        actor = f.admin()
        with acting_as(actor):
            school_services.make_year_current(new, actor)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual((old.status, new.status), ("closed", "current"))
        self.assertEqual(AuditLog.objects.filter(action__startswith="academic_year.").count(), 2)
