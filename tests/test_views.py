"""The pages work end to end and refuse what the role does not allow."""
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from learners.models import Learner

from . import factories as f


class Pages(TestCase):
    def setUp(self):
        self.admin = f.admin()
        self.class_4a = f.school_class("4", "A")
        self.learner = f.learner(self.admin, self.class_4a)
        self.client.force_login(self.admin)

    def test_main_pages_render(self):
        pages = [
            reverse("home"),
            reverse("learner_list"),
            reverse("learner_list") + "?q=" + self.learner.last_name,
            reverse("learner_list") + "?q=KLT-0000000",
            reverse("learner_register"),
            reverse("learner_import"),
            reverse("staff_list"),
            reverse("staff_create"),
            reverse("staff_detail", args=[self.admin.pk]),
            reverse("learner_edit", args=[self.learner.pk]),
            reverse("guardian_add", args=[self.learner.pk]),
            reverse("admin:index"),
            reverse("admin:school_schoolclass_changelist"),
            reverse("admin:accounts_teacherassignment_add"),
            reverse("admin:audit_auditlog_changelist"),
        ]
        for tab in ("personal", "guardians", "enrolment", "notes"):
            pages.append(reverse("learner_detail", args=[self.learner.pk]) + f"?tab={tab}")
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_register_through_the_form(self):
        data = {
            "first_name": "Natasha", "last_name": "Banda", "gender": "F", "date_of_birth": "2012-11-25",
            "registration_date": self.class_4a.academic_year.start_date.isoformat(),
            "school_class": self.class_4a.pk,
            "guardian_name": "Peter Banda", "guardian_phone": "0961112233", "guardian_relationship": "Father",
        }
        response = self.client.post(reverse("learner_register"), data)
        learner = Learner.objects.get(first_name="Natasha")
        self.assertRedirects(response, reverse("learner_detail", args=[learner.pk]))

        # Same family again: the page asks about the existing guardian, then warns of a duplicate.
        response = self.client.post(reverse("learner_register"), data)
        self.assertContains(response, "already belongs to a guardian")
        response = self.client.post(reverse("learner_register"), {**data, "guardian_choice": "new"})
        self.assertContains(response, "may already be registered")
        self.assertEqual(Learner.objects.filter(first_name="Natasha").count(), 1)

        response = self.client.post(
            reverse("learner_register"), {**data, "guardian_choice": "new", "override_reason": "Twin, confirmed"}
        )
        self.assertEqual(Learner.objects.filter(first_name="Natasha").count(), 2)

    def test_learner_number_search_flags_typing_mistake(self):
        number = self.learner.learner_number
        typo = number[:-1] + str((int(number[-1]) + 1) % 10)
        response = self.client.get(reverse("learner_list"), {"q": typo})
        self.assertContains(response, "not a valid learner number")
        response = self.client.get(reverse("learner_list"), {"q": number})
        self.assertContains(response, self.learner.first_name)

    def test_support_notes_tab_writes_audit(self):
        self.client.get(reverse("note_add", args=[self.learner.pk]))
        self.client.post(reverse("note_add", args=[self.learner.pk]), {"category": "medical", "note": "Asthma"})
        self.client.get(reverse("learner_detail", args=[self.learner.pk]) + "?tab=notes")
        self.assertEqual(AuditLog.objects.filter(action="support_note.view").count(), 1)


class TeacherPages(TestCase):
    def setUp(self):
        admin = f.admin()
        self.mine = f.learner(admin, f.school_class("4", "A"))
        self.other = f.learner(admin, f.school_class("4", "B"))
        self.teacher = f.teacher()
        f.assign(self.teacher, f.class_subject(self.mine.enrollments.get().school_class), admin)
        self.client.force_login(self.teacher)

    def test_list_shows_only_own_classes(self):
        response = self.client.get(reverse("learner_list"))
        self.assertContains(response, self.mine.last_name)
        self.assertNotContains(response, self.other.last_name)

    def test_other_learner_is_not_found(self):
        self.assertEqual(self.client.get(reverse("learner_detail", args=[self.mine.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("learner_detail", args=[self.other.pk])).status_code, 404)

    def test_cannot_register_edit_or_manage_staff(self):
        for url in (
            reverse("learner_register"),
            reverse("learner_edit", args=[self.mine.pk]),
            reverse("staff_list"),
            reverse("staff_create"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_own_profile_visible(self):
        self.assertEqual(self.client.get(reverse("staff_detail", args=[self.teacher.pk])).status_code, 200)

    def test_notes_tab_hidden(self):
        url = reverse("learner_detail", args=[self.mine.pk]) + "?tab=notes"
        self.assertEqual(self.client.get(url).status_code, 404)
