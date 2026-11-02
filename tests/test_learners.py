import datetime

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditLog
from core.context import acting_as
from learners import numbers, services
from learners.models import Guardian, GuardianContact, Learner, LearnerNumberSequence

from . import factories as f


class LearnerNumbers(TestCase):
    def test_luhn_check_digit(self):
        # 7992739871 is the textbook Luhn example: its check digit is 3.
        self.assertEqual(numbers.luhn_digit("7992739871"), "3")

    def test_format(self):
        number = numbers.format_number("KLT", 123)
        self.assertEqual(number[:10], "KLT-000123")
        self.assertEqual(len(number), 11)
        self.assertTrue(numbers.is_valid(number))

    def test_single_typing_mistake_detected(self):
        number = numbers.format_number("KLT", 4521)
        digits = number[4:]
        for i in range(len(digits)):
            wrong = digits[:i] + str((int(digits[i]) + 1) % 10) + digits[i + 1:]
            self.assertFalse(numbers.is_valid(f"KLT-{wrong}"), wrong)

    def test_neighbour_swap_detected(self):
        number = numbers.format_number("KLT", 123456)
        digits = number[4:]
        swapped = digits[1] + digits[0] + digits[2:]
        if swapped != digits:
            self.assertFalse(numbers.is_valid(f"KLT-{swapped}"))

    def test_numbers_increase_and_rollback_leaves_gap(self):
        school = f.school()
        with transaction.atomic():
            first = numbers.next_number(school)
        try:
            with transaction.atomic():
                numbers.next_number(school)
                raise RuntimeError
        except RuntimeError:
            pass
        with transaction.atomic():
            third = numbers.next_number(school)
        self.assertEqual(first[4:10], "000001")
        self.assertEqual(third[4:10], "000002")  # the rolled-back number is reused, not skipped, inside one test
        self.assertEqual(LearnerNumberSequence.objects.get(school=school).last_value, 2)


class Registration(TestCase):
    def setUp(self):
        self.actor = f.academic_admin()
        self.class_4a = f.school_class("4", "A")

    def register(self, **kwargs):
        kwargs.setdefault("guardians", [f.guardian_input()])
        fields = {"first_name": "Chilufya", "last_name": "Mwansa", "gender": "F", "date_of_birth": datetime.date(2016, 3, 14)}
        fields.update(kwargs.pop("fields", {}))
        with acting_as(self.actor):
            return services.register(self.actor, school_class=self.class_4a, **kwargs, **fields)

    def test_creates_learner_guardian_contact_link_enrollment_and_audit(self):
        learner = self.register()
        self.assertTrue(learner.learner_number.startswith("KLT-"))
        self.assertTrue(numbers.is_valid(learner.learner_number))
        link = learner.guardian_links.get()
        self.assertEqual(link.contact_role, "primary")
        self.assertEqual(link.guardian.current_contact().phone, "+260971234567")
        self.assertFalse(link.guardian.current_contact().is_verified)
        enrollment = learner.enrollments.get()
        self.assertEqual(enrollment.school_class, self.class_4a)
        self.assertEqual(enrollment.status, "active")
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"learner.create", "guardian.create", "learner_guardian.link", "enrollment.create"} <= actions)
        entry = AuditLog.objects.get(action="learner.create")
        self.assertEqual(entry.actor, self.actor)
        self.assertEqual(entry.object_id, learner.pk)

    def test_exactly_one_primary_contact_required(self):
        with self.assertRaises(ValidationError):
            self.register(guardians=[f.guardian_input(role="alternative")])

    def test_teacher_cannot_register(self):
        self.actor = f.teacher()
        with self.assertRaises(PermissionDenied):
            self.register()

    def test_duplicate_by_birth_date_and_similar_name(self):
        first = self.register()
        with self.assertRaises(services.LikelyDuplicate) as caught:
            self.register(fields={"first_name": "Chilufiya"}, guardians=[f.guardian_input(name="Someone Else", phone="0961112233")])
        self.assertEqual([m for m, _ in caught.exception.matches], [first])

    def test_duplicate_by_guardian_phone_and_similar_first_name(self):
        first = self.register()
        with self.assertRaises(services.LikelyDuplicate) as caught:
            self.register(fields={"last_name": "Banda", "date_of_birth": datetime.date(2014, 1, 1)})
        self.assertIn("guardian phone", caught.exception.matches[0][1])
        self.assertEqual(caught.exception.matches[0][0], first)

    def test_sibling_with_same_guardian_is_not_a_duplicate(self):
        self.register()
        sibling = self.register(fields={"first_name": "Mwila", "gender": "M", "date_of_birth": datetime.date(2018, 7, 2)})
        self.assertEqual(Learner.objects.count(), 2)
        self.assertEqual(sibling.guardian_links.count(), 1)

    def test_override_needs_reason_and_is_audited(self):
        self.register()
        twin = self.register(
            fields={"first_name": "Chilufya", "other_names": "Twin"},
            override_reason="Twin sister, confirmed with the mother",
        )
        entry = AuditLog.objects.get(action="duplicate.override")
        self.assertEqual(entry.object_id, twin.pk)
        self.assertEqual(entry.reason, "Twin sister, confirmed with the mother")

    def test_override_refused_without_permission(self):
        self.register()
        clerk = f.teacher()
        clerk.user_permissions.add(Permission.objects.get(codename="add_learner"))
        self.actor = User.objects.get(pk=clerk.pk)  # reload: permissions are cached per instance
        with self.assertRaises(PermissionDenied):
            self.register(override_reason="I think it's fine")

    def test_existing_guardian_reused_when_chosen(self):
        first = self.register()
        guardian = first.guardian_links.get().guardian
        sibling = self.register(
            fields={"first_name": "Mwila", "gender": "M", "date_of_birth": datetime.date(2018, 7, 2)},
            guardians=[services.GuardianInput(relationship="Mother", contact_role="primary", existing=guardian)],
        )
        self.assertEqual(sibling.guardian_links.get().guardian, guardian)
        self.assertEqual(Guardian.objects.count(), 1)

    def test_class_in_closed_year_refused(self):
        self.class_4a.academic_year.status = "closed"
        self.class_4a.academic_year.save()
        with self.assertRaises(ValidationError):
            self.register()


class Guardians(TestCase):
    def setUp(self):
        self.actor = f.academic_admin()
        self.learner = f.learner(self.actor)
        self.primary = self.learner.guardian_links.get()
        with acting_as(self.actor):
            self.second = services.add_guardian(self.actor, self.learner, f.guardian_input(name="Peter Mwansa", phone="0961112233", role="alternative", relationship="Father"))

    def roles(self):
        return {link.guardian.full_name: link.contact_role for link in self.learner.guardian_links.filter(status="active")}

    def test_making_alternative_primary_swaps_them(self):
        with acting_as(self.actor):
            services.set_contact_role(self.actor, self.second, "primary")
        self.assertEqual(self.roles()[self.second.guardian.full_name], "primary")
        self.assertEqual(self.roles()[self.primary.guardian.full_name], "alternative")
        self.assertTrue(AuditLog.objects.filter(action="learner_guardian.contact_role").exists())

    def test_primary_cannot_be_dropped_without_replacement(self):
        with self.assertRaises(ValidationError):
            services.set_contact_role(self.actor, self.primary, "none")

    def test_primary_cannot_be_removed(self):
        with self.assertRaises(ValidationError):
            services.remove_link(self.actor, self.primary, "moved away")

    def test_removing_keeps_the_row(self):
        with acting_as(self.actor):
            services.remove_link(self.actor, self.second, "Parents separated; court order")
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, "removed")
        self.assertEqual(self.second.removed_by, self.actor)

    def test_phone_change_keeps_history_and_needs_reverification(self):
        guardian = self.primary.guardian
        old = guardian.current_contact()
        with acting_as(self.actor):
            services.verify_contact(self.actor, old, "in_person")
            new = services.change_phone(self.actor, guardian, "0955 123 456")
        old.refresh_from_db()
        self.assertFalse(old.is_current)
        self.assertIsNotNone(old.retired_at)
        self.assertTrue(old.is_verified)
        self.assertEqual(new.phone, "+260955123456")
        self.assertFalse(new.is_verified)
        self.assertEqual(GuardianContact.objects.filter(guardian=guardian).count(), 2)

    def test_add_second_primary_refused(self):
        with self.assertRaises(ValidationError):
            services.add_guardian(self.actor, self.learner, f.guardian_input(name="Aunt", phone="0977000111", role="primary"))


class Enrollments(TestCase):
    def setUp(self):
        self.actor = f.academic_admin()
        self.class_5a = f.school_class("5", "A")
        self.class_5b = f.school_class("5", "B")
        self.learner = f.learner(self.actor, self.class_5a, registration_date=self.class_5a.academic_year.start_date)
        self.enrollment = self.learner.enrollments.get()

    def test_transfer_ends_old_and_starts_new(self):
        effective = self.enrollment.start_date + datetime.timedelta(days=60)
        with acting_as(self.actor):
            new = services.transfer(self.actor, self.enrollment, self.class_5b, effective, "Class sizes")
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ended")
        self.assertEqual(self.enrollment.end_reason, "transfer")
        self.assertEqual(self.enrollment.end_date, effective - datetime.timedelta(days=1))
        self.assertEqual(new.start_date, effective)
        self.assertEqual(new.previous_enrollment, self.enrollment)
        self.assertEqual(self.learner.current_enrollment(), new)

    def test_transfer_to_another_year_refused(self):
        next_year = f.year("2099", status="planned", start=datetime.date(2099, 1, 1))
        with self.assertRaises(ValidationError):
            services.transfer(self.actor, self.enrollment, f.school_class("6", "A", next_year), datetime.date(2099, 2, 1))

    def test_correction_reverses_and_replaces(self):
        with acting_as(self.actor):
            new = services.correct(self.actor, self.enrollment, self.class_5b, "Typed the wrong stream")
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "reversed")
        self.assertEqual(new.start_date, self.enrollment.start_date)
        self.assertEqual(new.school_class, self.class_5b)

    def test_second_overlapping_enrollment_refused(self):
        with self.assertRaises(ValidationError):
            services.enroll(self.actor, self.learner, self.class_5b, timezone.localdate())

    def test_leaving_ends_enrollment(self):
        leave = self.enrollment.start_date + datetime.timedelta(days=30)
        with acting_as(self.actor):
            services.change_status(self.actor, self.learner, "transferred", "Family moved to Ndola", leave)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ended")
        self.assertEqual(self.enrollment.end_date, leave - datetime.timedelta(days=1))
        self.learner.refresh_from_db()
        self.assertEqual(self.learner.status, "transferred")
