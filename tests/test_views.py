import datetime

from django.test import TestCase
from django.urls import reverse

from accounts import roles
from core.models import AuditLog
from learners.models import Learner

from . import factories as f

D = datetime.date


class ScreenTests(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        f.year()
        self.c3a, self.c3b = f.make_class("3"), f.make_class("3", "B")
        self.learner = f.register(self.admin, self.c3a)
        self.other = f.register(self.admin, self.c3b, first="Natasha", last="Banda", phone="0961112233",
                                guardian="Peter Banda", dob=D(2017, 1, 9))
        self.head = f.make_user("head", roles.HEADTEACHER)
        self.teacher = f.make_user("teacher", roles.TEACHER)
        from staffing import services

        services.assign_teacher(f.class_subject(self.c3a, f.subject("3")), self.teacher, "primary", D(2026, 1, 5), self.admin)

    def login(self, user):
        self.client.force_login(user)

    def test_every_screen_loads_for_the_system_administrator(self):
        self.login(self.admin)
        pages = ["/", "/learners/", "/learners/register/", "/learners/import/", "/learners/import/template.xlsx",
                 f"/learners/{self.learner.pk}/", "/staff/", "/staff/new/", f"/staff/{self.teacher.pk}/",
                 "/assignments/", "/audit/", "/setup/", "/setup/school/academicyear/", "/password/"]
        pages += [f"/learners/{self.learner.pk}/?tab={t}" for t in ["guardians", "enrollments", "attendance"]]
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_register_through_the_screen(self):
        self.login(self.head)
        data = {"first_name": "Bwalya", "last_name": "Zulu", "date_of_birth": "2018-05-05", "gender": "M",
                "class_group": self.c3a.pk, "start_date": "2026-02-01", "guardian_name": "Ann Zulu",
                "relationship": "Mother", "guardian_phone": "0977000111"}
        response = self.client.post("/learners/register/", data)
        learner = Learner.objects.get(first_name="Bwalya")
        self.assertRedirects(response, f"/learners/{learner.pk}/?tab=personal")

    def test_shared_phone_asks_staff_to_choose_d3(self):
        self.login(self.head)
        data = {"first_name": "Mwila", "last_name": "Mwansa", "date_of_birth": "2019-07-02", "class_group": self.c3a.pk,
                "start_date": "2026-02-01", "guardian_name": "Mary Mwansa", "guardian_phone": "0971234567"}
        response = self.client.post("/learners/register/", data)
        self.assertContains(response, "This mobile number is already used by")
        mary = self.learner.guardians.get()
        response = self.client.post("/learners/register/", {**data, "guardian_choice": mary.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mary.learners.count(), 2)

    def test_duplicate_warning_on_screen(self):
        self.login(self.head)
        data = {"first_name": "Chilufya", "last_name": "Mwanza", "date_of_birth": "2016-03-14", "class_group": self.c3a.pk,
                "start_date": "2026-02-01", "guardian_name": "X", "guardian_phone": "0951234567"}
        response = self.client.post("/learners/register/", data)
        self.assertContains(response, "This learner may already be registered")
        self.assertContains(response, self.learner.learner_number)
        self.client.post("/learners/register/", {**data, "override_reason": "Different child, confirmed with parent"})
        self.assertEqual(Learner.objects.filter(last_name="Mwanza").count(), 1)

    def test_teacher_sees_only_their_class_and_cannot_register(self):
        self.login(self.teacher)
        self.assertContains(self.client.get("/learners/"), self.learner.learner_number)
        self.assertNotContains(self.client.get("/learners/"), self.other.learner_number)
        self.assertEqual(self.client.get(f"/learners/{self.other.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/learners/{self.learner.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/learners/register/").status_code, 403)
        self.assertEqual(self.client.get("/staff/").status_code, 403)
        self.assertEqual(self.client.get("/audit/").status_code, 403)
        self.assertEqual(self.client.get("/setup/").status_code, 302)  # no admin site for teachers

    def test_headteacher_can_view_structure_but_not_change_it(self):
        self.login(self.head)
        self.assertEqual(self.client.get("/setup/school/academicyear/").status_code, 200)
        self.assertEqual(self.client.get("/setup/school/academicyear/add/").status_code, 403)

    def test_headteacher_role_choices_follow_d6(self):
        self.login(self.head)
        page = self.client.get("/staff/new/").content.decode()
        self.assertIn('value="Teacher"', page)
        self.assertIn('value="Academic Administrator"', page)
        self.assertNotIn('value="Headteacher"', page)
        self.assertNotIn('value="System Administrator"', page)
        self.assertEqual(self.client.get(f"/staff/{self.admin.pk}/edit/").status_code, 403)

    def test_check_digit_hint_on_search(self):
        self.login(self.admin)
        number = self.learner.learner_number
        typo = number[:-1] + str((int(number[-1]) + 1) % 10)
        self.assertContains(self.client.get("/learners/", {"q": typo}), "last digit doesn")
        self.assertContains(self.client.get("/learners/", {"q": number}), self.learner.full_name)

    def test_opening_support_notes_is_audited(self):
        from learners.services import add_support_note

        add_support_note(self.learner, "medical", "Allergic to penicillin", self.admin)
        self.login(self.head)
        page = self.client.get(f"/learners/{self.learner.pk}/?tab=personal")
        self.assertNotContains(page, "penicillin")
        page = self.client.get(f"/learners/{self.learner.pk}/?tab=personal&notes=show")
        self.assertContains(page, "penicillin")
        self.assertTrue(AuditLog.objects.filter(action="support_note.view", actor=self.head).exists())

    def test_admin_changes_are_audited(self):
        self.login(self.admin)
        year = self.c3a.academic_year
        self.client.post(f"/setup/school/academicyear/{year.pk}/change/", {
            "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-30",
            "terms-TOTAL_FORMS": "0", "terms-INITIAL_FORMS": "0", "terms-MIN_NUM_FORMS": "0", "terms-MAX_NUM_FORMS": "1000",
        })
        log = AuditLog.objects.get(action="academicyear.update")
        self.assertEqual(log.changes["end_date"], ["2026-12-31", "2026-12-30"])

    def test_import_through_the_screens(self):
        from .test_import import GOOD, xlsx

        f.make_class("2")
        f.make_class("4")
        f.make_class("F1")
        self.login(self.head)
        response = self.client.post(reverse("learner_import"), {"file": xlsx(GOOD)})
        self.assertRedirects(response, reverse("learner_import_preview"))
        preview = self.client.get(reverse("learner_import_preview"))
        self.assertContains(preview, "ready to import")
        self.assertContains(preview, "likely duplicate")  # Chilufya and Natasha are already registered
        done = self.client.post(reverse("learner_import_preview"), {"override_reason": ""})
        self.assertContains(done, "learners registered")
        self.assertEqual(done.context["result"].created[0][1].first_name, "Mwila")
        self.assertEqual(len(done.context["result"].skipped_duplicates), 2)
        self.assertEqual(Learner.objects.filter(first_name="Natasha").count(), 1)
