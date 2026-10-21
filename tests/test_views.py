from django.test import TestCase
from django.urls import reverse

from accounts import roles
from people.models import Student

from .factories import make_user

PUPIL = {
    "admission_number": "2026-010", "first_name": "Bwalya", "other_names": "", "last_name": "Zulu",
    "gender": "M", "date_of_birth": "2015-05-05",
    "guardian_name": "Ann Zulu", "guardian_phone": "0977000111", "guardian_email": "", "relationship": "Mother",
}


class ViewAccessTests(TestCase):
    def login(self, role):
        user = make_user(role.lower(), role)
        self.client.force_login(user)
        return user

    def test_home_requires_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

    def test_login_page_and_sign_in(self):
        make_user("head", roles.HEAD)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        response = self.client.post(reverse("login"), {"username": "head", "password": "pass-12345!"})
        self.assertRedirects(response, reverse("home"))

    def test_head_can_view_but_not_add(self):
        self.login(roles.HEAD)
        self.assertContains(self.client.get(reverse("home")), "School overview")
        self.assertEqual(self.client.get(reverse("student_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("student_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:people_student_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:people_student_add")).status_code, 403)

    def test_teacher_has_no_admin_site(self):
        self.login(roles.TEACHER)
        self.assertContains(self.client.get(reverse("home")), "My classes")
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)

    def test_admin_adds_pupil_with_guardian(self):
        self.login(roles.ADMIN)
        response = self.client.post(reverse("student_add"), PUPIL)
        self.assertRedirects(response, reverse("student_list"))
        pupil = Student.objects.get(admission_number="2026-010")
        self.assertEqual(pupil.primary_guardian.phone, "+260977000111")
        self.assertContains(self.client.get(reverse("student_list")), "Bwalya Zulu")

    def test_add_pupil_rejects_bad_phone(self):
        self.login(roles.ADMIN)
        response = self.client.post(reverse("student_add"), {**PUPIL, "guardian_phone": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not a valid Zambian mobile number")
        self.assertFalse(Student.objects.exists())
