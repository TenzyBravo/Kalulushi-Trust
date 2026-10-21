from django.contrib.auth.models import Group
from django.test import TestCase

from accounts import roles
from accounts.models import User

from .factories import make_user


class RoleGroupTests(TestCase):
    def test_groups_exist_after_migrate(self):
        names = set(Group.objects.values_list("name", flat=True))
        self.assertTrue({"Admin", "Head", "Teacher"} <= names)

    def test_admin_has_all_school_permissions(self):
        admin = make_user("admin", roles.ADMIN)
        for perm in ["people.add_student", "people.delete_guardian", "school.change_academicyear",
                     "academics.add_gradeband", "accounts.add_user"]:
            self.assertTrue(admin.has_perm(perm), perm)

    def test_head_is_view_only(self):
        head = make_user("head", roles.HEAD)
        self.assertTrue(head.has_perm("people.view_student"))
        self.assertTrue(head.has_perm("academics.view_gradingscale"))
        self.assertFalse(head.has_perm("people.add_student"))
        self.assertFalse(head.has_perm("school.change_term"))
        perms = Group.objects.get(name="Head").permissions.all()
        self.assertTrue(perms.exists())
        self.assertTrue(all(p.codename.startswith("view_") for p in perms))

    def test_teacher_permissions(self):
        teacher = make_user("teacher", roles.TEACHER)
        self.assertTrue(teacher.has_perm("people.view_student"))
        self.assertFalse(teacher.has_perm("people.view_staff"))
        self.assertFalse(teacher.has_perm("people.add_student"))

    def test_admin_site_access_follows_role(self):
        self.assertTrue(make_user("a", roles.ADMIN).is_staff)
        self.assertTrue(make_user("h", roles.HEAD).is_staff)
        self.assertFalse(make_user("t", roles.TEACHER).is_staff)

    def test_changing_role_moves_group(self):
        user = make_user("x", roles.TEACHER)
        user.role = roles.HEAD
        user.save()
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["Head"])
        self.assertTrue(user.is_staff)

    def test_superuser_defaults_to_admin_role(self):
        su = User.objects.create_superuser("root", "root@example.com", "pass-12345!")
        self.assertEqual(su.role, roles.ADMIN)
        self.assertTrue(su.is_staff)
