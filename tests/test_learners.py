import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts import roles
from core.models import AuditLog
from learners import numbers, services
from learners.models import Learner

from . import factories as f

D = datetime.date


class NumberTests(TestCase):
    def test_format_and_check_digit(self):
        number = numbers.format_number("KLT", 123)
        self.assertRegex(number, r"^KLT-000123\d$")
        self.assertTrue(numbers.is_valid(number))

    def test_check_digit_catches_typing_mistakes(self):
        number = numbers.format_number("KLT", 4527)  # KLT-004527?
        body = number[4:10]
        wrong_digit = number[:9] + str((int(number[9]) + 1) % 10) + number[10]
        swapped = number[:4] + body[:3] + body[4] + body[3] + body[5:] + number[10]
        self.assertFalse(numbers.is_valid(wrong_digit))
        if swapped != number:
            self.assertFalse(numbers.is_valid(swapped))

    def test_numbers_are_sequential_and_permanent(self):
        admin = f.admin_user()
        f.year()
        c = f.make_class("1")
        a = f.register(admin, c)
        b = f.register(admin, c, first="Mwila", last="Phiri", dob=D(2019, 2, 2), phone="0961112233", guardian="Ann Phiri")
        self.assertEqual(a.learner_number, numbers.format_number("KLT", 1))
        self.assertEqual(b.learner_number, numbers.format_number("KLT", 2))
        services.update_learner(a, {"first_name": "Chilu"}, admin)
        a.refresh_from_db()
        self.assertEqual(a.learner_number, numbers.format_number("KLT", 1))


class RegistrationTests(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        f.year()
        self.c5a = f.make_class("5")

    def test_registration_creates_learner_guardian_and_enrollment(self):
        learner = f.register(self.admin, self.c5a)
        link = learner.guardian_links.get()
        self.assertEqual(link.contact_role, "primary")
        self.assertEqual(link.guardian.current_phone, "+260971234567")
        self.assertFalse(link.guardian.current_contact().is_verified)
        enrollment = learner.current_enrollment()
        self.assertEqual((enrollment.class_group, enrollment.start_date), (self.c5a, D(2026, 1, 15)))
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"learner.create", "guardian.create", "guardian_link.create", "enrollment.create"} <= actions)

    def test_enrollment_date_must_be_in_the_year(self):
        with self.assertRaises(ValidationError):
            f.register(self.admin, self.c5a, start=D(2025, 12, 1))
        self.assertFalse(Learner.objects.exists())

    def test_duplicate_by_birth_date_and_similar_name(self):
        first = f.register(self.admin, self.c5a)
        with self.assertRaises(services.DuplicateWarning) as ctx:
            f.register(self.admin, self.c5a, first="Chilufya", last="Mwanza", phone="0951234567", guardian="Someone")
        self.assertEqual(ctx.exception.matches[0][0], first)

    def test_duplicate_by_guardian_phone_and_first_name(self):
        f.register(self.admin, self.c5a)
        with self.assertRaises(services.DuplicateWarning):
            f.register(self.admin, self.c5a, first="Chilufya", last="Banda", dob=None)

    def test_sibling_sharing_a_phone_is_not_a_duplicate(self):
        f.register(self.admin, self.c5a)
        f.register(self.admin, self.c5a, first="Mwila", last="Mwansa", dob=D(2018, 7, 2))

    def test_override_needs_reason_and_permission_and_is_audited(self):
        f.register(self.admin, self.c5a)
        teacher_registrar = f.make_user("t", roles.TEACHER)
        with self.assertRaises(PermissionDenied):
            f.register(teacher_registrar, self.c5a, override_reason="twins", first="Chilufya", last="Mwanza", phone="0951234567")
        twin = f.register(self.admin, self.c5a, override_reason="Twins: Chilufya and Chileshe", first="Chilufya",
                          last="Mwanza", phone="0951234567")
        log = AuditLog.objects.get(action="duplicate.override")
        self.assertEqual((log.object_id, log.reason), (twin.pk, "Twins: Chilufya and Chileshe"))

    def test_support_note_reads_are_audited(self):
        learner = f.register(self.admin, self.c5a)
        services.add_support_note(learner, "medical", "Epilepsy: call guardian", self.admin)
        services.view_support_notes(learner, self.admin)
        self.assertTrue(AuditLog.objects.filter(action="support_note.view", object_id=learner.pk).exists())
        self.assertNotIn("Epilepsy", str(list(AuditLog.objects.values_list("changes", flat=True))))

    def test_status_change_needs_reason(self):
        learner = f.register(self.admin, self.c5a)
        with self.assertRaises(ValidationError):
            services.change_status(learner, "withdrawn", "", self.admin)
        services.change_status(learner, "withdrawn", "Family moved to Ndola", self.admin)
        learner.refresh_from_db()
        self.assertEqual(learner.status, "withdrawn")


class GuardianTests(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        f.year()
        self.c = f.make_class("3")
        self.learner = f.register(self.admin, self.c)
        self.mum = self.learner.guardians.get()

    def test_shared_phone_is_allowed_d3(self):
        other = services.create_guardian(self.learner.school, "Grandmother Mwansa", "0971234567", "", self.admin)
        self.assertEqual(other.current_phone, self.mum.current_phone)

    def test_changing_number_keeps_history_and_needs_reverification(self):
        services.verify_contact(self.mum.current_contact(), "in_person", self.admin)
        services.change_phone(self.mum, "0761234567", self.admin)
        contacts = list(self.mum.contacts.order_by("created_at"))
        self.assertEqual([c.phone for c in contacts], ["+260971234567", "+260761234567"])
        self.assertFalse(contacts[0].is_current)
        self.assertTrue(contacts[0].is_verified)
        self.assertFalse(contacts[1].is_verified)

    def test_make_primary_moves_the_role(self):
        dad = services.create_guardian(self.learner.school, "John Mwansa", "0961112233", "", self.admin)
        link = services.link_guardian(self.learner, dad, self.admin, relationship="Father")
        services.set_contact_role(link, "primary", self.admin)
        roles_now = dict(self.learner.guardian_links.values_list("guardian__full_name", "contact_role"))
        self.assertEqual(roles_now, {"Mary Mwansa": "none", "John Mwansa": "primary"})

    def test_removing_a_guardian_keeps_the_record(self):
        link = self.learner.guardian_links.get()
        services.remove_link(link, "Court order", self.admin)
        link.refresh_from_db()
        self.assertEqual((link.status, link.removal_reason, link.contact_role), ("removed", "Court order", "none"))
        services.link_guardian(self.learner, self.mum, self.admin)  # can be linked again later


class EnrollmentTests(TestCase):
    def setUp(self):
        self.admin = f.admin_user()
        f.year()
        self.c5a, self.c5b = f.make_class("5"), f.make_class("5", "B")
        self.learner = f.register(self.admin, self.c5a)

    def test_transfer_keeps_the_old_enrollment(self):
        new = services.transfer(self.learner, self.c5b, D(2026, 6, 15), "Class sizes", self.admin)
        old = new.previous_enrollment
        self.assertEqual((old.end_date, old.status, old.end_reason), (D(2026, 6, 14), "ended", "transfer"))
        self.assertEqual((new.class_group, new.start_date, new.approved_by), (self.c5b, D(2026, 6, 15), self.admin))
        self.assertEqual(self.learner.current_enrollment(), new)

    def test_transfer_needs_an_approver(self):
        academic_admin = f.make_user("aa", roles.ACADEMIC_ADMIN)
        with self.assertRaises(PermissionDenied):
            services.transfer(self.learner, self.c5b, D(2026, 6, 15), "x", academic_admin)

    def test_transfer_to_another_year_is_refused(self):
        next_year = f.year("2027", status="planned")
        c6a = f.make_class("6", academic_year=next_year)
        with self.assertRaises(ValidationError):
            services.transfer(self.learner, c6a, D(2027, 1, 10), "x", self.admin)

    def test_correction_reverses_and_reenrolls(self):
        wrong = self.learner.current_enrollment()
        services.reverse_enrollment(wrong, "Should have been 5B", self.admin)
        services.enroll(self.learner, self.c5b, D(2026, 1, 15), "Correct class", self.admin)
        statuses = sorted(self.learner.enrollments.values_list("class_group__name", "status"))
        self.assertEqual(statuses, [("5A", "reversed"), ("5B", "active")])

    def test_cannot_enroll_twice(self):
        with self.assertRaises(ValidationError):
            services.enroll(self.learner, self.c5b, D(2026, 3, 1), "x", self.admin)
