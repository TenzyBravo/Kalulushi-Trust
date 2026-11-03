"""The Permissions page and individual extra permissions."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts import permission_catalogue, roles, services
from accounts.models import User
from core.models import AuditLog
from staffing import access

from . import factories as f


def fresh(user):
    return User.objects.get(pk=user.pk)  # permissions are cached per instance


class Catalogue(TestCase):
    def test_lists_every_module_without_delete_permissions(self):
        modules = dict(permission_catalogue.catalogue())
        self.assertIn("Attendance and follow-up", modules)
        codes = [row["code"] for rows in modules.values() for row in rows]
        self.assertFalse([c for c in codes if ".delete_" in c])

    def test_support_notes_row(self):
        rows = {r["code"]: r for _, rs in permission_catalogue.catalogue() for r in rs}
        notes = rows["learners.view_support_notes"]
        self.assertTrue(notes["sensitive"] and notes["grantable"])
        self.assertIn(roles.HEADTEACHER, notes["roles"])
        self.assertNotIn(roles.TEACHER, notes["roles"])
        self.assertEqual(rows["learners.view_learner"]["kind"], "view")
        self.assertEqual(rows["accounts.manage_staff"]["kind"], "admin")


class ExtraPermissions(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        self.head = f.make_user("head", roles.HEADTEACHER)
        self.teacher = f.make_user("teacher", roles.TEACHER)
        f.year()
        self.c3a = f.make_class("3")
        self.learner = f.register(self.admin, self.c3a)
        from staffing import services as staffing
        import datetime

        staffing.assign_teacher(f.class_subject(self.c3a, f.subject("3")), self.teacher, "primary",
                                datetime.date(2026, 1, 5), self.admin)

    def test_grant_support_notes_to_a_teacher(self):
        self.assertFalse(access.can_view_support_notes(fresh(self.teacher), self.learner))
        services.grant_permission(self.admin, self.teacher, "learners.view_support_notes", "Handles first aid for 3A")
        self.assertTrue(access.can_view_support_notes(fresh(self.teacher), self.learner))
        entry = AuditLog.objects.get(action="user.permission_grant")
        self.assertEqual((entry.reason, entry.object_id), ("Handles first aid for 3A", self.teacher.pk))
        services.revoke_permission(self.admin, self.teacher, "learners.view_support_notes", "No longer first aider")
        self.assertFalse(fresh(self.teacher).has_perm("learners.view_support_notes"))
        self.assertTrue(AuditLog.objects.filter(action="user.permission_revoke").exists())

    def test_rules(self):
        with self.assertRaisesMessage(ValidationError, "reason"):
            services.grant_permission(self.admin, self.teacher, "learners.view_support_notes", " ")
        with self.assertRaisesMessage(ValidationError, "only with a role"):
            services.grant_permission(self.admin, self.teacher, "accounts.manage_staff", "no")
        with self.assertRaises(PermissionDenied):  # D6: a Headteacher does not manage System Administrators
            services.grant_permission(self.head, self.admin, "core.view_audit_log", "x")
        aa = f.make_user("aa", roles.ACADEMIC_ADMIN)
        with self.assertRaises(PermissionDenied):  # no manage_staff
            services.grant_permission(aa, self.teacher, "learners.import_learners", "x")
        with self.assertRaisesMessage(ValidationError, "through their role"):
            services.grant_permission(self.admin, aa, "learners.import_learners", "x")

    def test_headteacher_grants_only_what_they_hold(self):
        services.grant_permission(self.head, self.teacher, "core.view_audit_log", "Helping with the term audit")
        self.assertTrue(fresh(self.teacher).has_perm("core.view_audit_log"))

    def test_pages(self):
        self.client.force_login(self.head)
        response = self.client.get("/staff/permissions/")
        self.assertContains(response, "learners.view_support_notes")
        self.assertContains(response, "Sensitive")
        response = self.client.get(f"/staff/{self.teacher.pk}/")
        self.assertContains(response, "Extra permissions")
        response = self.client.post(f"/staff/{self.teacher.pk}/permissions/", {
            "code": "learners.view_support_notes", "action": "grant", "reason": "Class first aider",
        })
        self.assertRedirects(response, f"/staff/{self.teacher.pk}/#permissions", fetch_redirect_response=False)
        self.assertTrue(fresh(self.teacher).has_perm("learners.view_support_notes"))
        self.assertContains(self.client.get("/staff/permissions/"), "+ 1 individual")

    def test_teachers_cannot_open_the_page(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get("/staff/permissions/").status_code, 403)
