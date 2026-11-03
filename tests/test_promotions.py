"""Year-end promotions and transfer requests (docs/database-design-promotions.md)."""
import datetime
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from accounts import roles
from core.models import AuditLog
from learners import promotions
from learners.models import EnrollmentRequest, Learner, PromotionBatch
from school.models import AcademicYear
from staffing import services as staffing

from . import factories as f

D = datetime.date
TODAY = D(2026, 11, 20)
_real_localdate = timezone.localdate
R = EnrollmentRequest


def fake_localdate(value=None, timezone=None):
    return TODAY if value is None else _real_localdate(value, timezone)


class PromotionTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", new=fake_localdate)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = f.admin_user()
        self.head = f.make_user("head", roles.HEADTEACHER)
        self.teacher = f.make_user("teacher", roles.TEACHER)
        self.year = f.year()
        self.next = AcademicYear.objects.create(school=f.school(), name="2027", start_date=D(2027, 1, 1),
                                                end_date=D(2027, 12, 31), status="planned")
        self.c5a, self.c5b, self.f4a = f.make_class("5"), f.make_class("5", "B"), f.make_class("F4")
        self.c6a = f.make_class("6", academic_year=self.next)
        self.next_5a = f.make_class("5", academic_year=self.next)
        staffing.assign_class_teacher(self.c5a, self.teacher, D(2026, 1, 12), self.admin)
        self.anna = f.register(self.admin, self.c5a)
        self.bwalya = f.register(self.admin, self.c5a, first="Bwalya", last="Banda", phone="0961112233",
                                 guardian="Peter Banda", dob=D(2016, 1, 9))

    def prepare(self, overrides=None, class_group=None, actor=None):
        class_group = class_group or self.c5a
        actor = actor or self.teacher
        batch = promotions.batch_for(class_group, actor=actor, create=True)
        overrides = overrides or {}
        entries = {}
        for enrollment, req in promotions.sheet_rows(batch):
            decision, to_class, note = overrides.get(enrollment.learner, (req.decision, req.to_class, ""))
            entries[enrollment.learner_id] = (decision, to_class, note)
        promotions.save_sheet(actor, batch, entries)
        return batch


class Sheets(PromotionTestCase):
    def test_defaults_same_stream_next_grade_and_complete_in_final_grade(self):
        self.assertEqual(promotions.suggested(self.c5a, self.next), (R.PROMOTE, self.c6a))
        self.assertEqual(promotions.suggested(self.f4a, self.next), (R.COMPLETE, None))
        f1a = f.make_class("F1", academic_year=self.next)
        self.assertEqual(promotions.suggested(f.make_class("7"), self.next), (R.PROMOTE, f1a))

    def test_submit_validates_every_decision(self):
        batch = self.prepare({self.bwalya: (R.REPEAT, self.next_5a, "")})
        with self.assertRaisesMessage(ValidationError, "add a note for 'repeat'"):
            promotions.submit_sheet(self.teacher, batch)
        self.prepare({self.bwalya: (R.REPEAT, self.c6a, "Weak in literacy")})
        with self.assertRaisesMessage(ValidationError, "repeat goes to Grade 5"):
            promotions.submit_sheet(self.teacher, batch)
        self.prepare({self.bwalya: (R.REPEAT, self.next_5a, "Weak in literacy")})
        promotions.submit_sheet(self.teacher, batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "submitted")
        self.assertEqual(set(batch.requests.values_list("status", flat=True)), {"pending"})

    def test_only_the_class_teacher_or_office_prepares(self):
        stranger = f.make_user("stranger", roles.TEACHER)
        with self.assertRaises(PermissionDenied):
            self.prepare(actor=stranger)
        self.prepare(actor=f.make_user("aa", roles.ACADEMIC_ADMIN))

    def test_no_next_year_no_sheet(self):
        with self.assertRaisesMessage(ValidationError, "Set up the academic year after 2027"):
            promotions.batch_for(self.c6a, actor=self.admin, create=True)  # nothing after 2027 yet


class Approval(PromotionTestCase):
    def submitted(self, overrides=None):
        batch = self.prepare(overrides)
        promotions.submit_sheet(self.teacher, batch)
        return batch

    def test_approval_ends_and_starts_enrollments_in_one_go(self):
        batch = self.submitted({self.bwalya: (R.REPEAT, self.next_5a, "Missed most of term 2")})
        promotions.approve_sheet(self.head, batch)
        old = self.anna.enrollments.get(academic_year=self.year)
        new = self.anna.enrollments.get(academic_year=self.next)
        self.assertEqual((old.end_date, old.status, old.end_reason), (D(2026, 12, 31), "ended", "promotion"))
        self.assertEqual((new.class_group, new.start_date, new.previous_enrollment, new.approved_by),
                         (self.c6a, D(2027, 1, 1), old, self.head))
        self.assertEqual(new.request.status, "approved")
        self.assertEqual(self.bwalya.enrollments.get(academic_year=self.next).class_group, self.next_5a)
        self.assertEqual(self.bwalya.enrollments.get(academic_year=self.year).end_reason, "repeat")
        self.assertTrue(AuditLog.objects.filter(action="promotion_batch.approve").exists())

    def test_only_the_headteacher_and_only_once(self):
        batch = self.submitted()
        with self.assertRaises(PermissionDenied):
            promotions.approve_sheet(self.admin, batch)
        promotions.approve_sheet(self.head, batch)
        with self.assertRaisesMessage(ValidationError, "approved"):
            promotions.approve_sheet(self.head, batch)
        self.assertEqual(self.anna.enrollments.filter(academic_year=self.next).count(), 1)

    def test_return_lets_the_teacher_fix_it(self):
        batch = self.submitted()
        with self.assertRaisesMessage(ValidationError, "needs changing"):
            promotions.return_sheet(self.head, batch, " ")
        promotions.return_sheet(self.head, batch, "Bwalya should repeat")
        self.prepare({self.bwalya: (R.REPEAT, self.next_5a, "Agreed with the Headteacher")})
        promotions.submit_sheet(self.teacher, batch)

    def test_hold_then_place_individually(self):
        batch = self.submitted({self.bwalya: (R.HOLD, None, "Parents to meet the Headteacher")})
        promotions.approve_sheet(self.head, batch)
        self.assertFalse(self.bwalya.enrollments.filter(academic_year=self.next).exists())
        held = promotions.held_requests()
        self.assertEqual([r.learner for r in held], [self.bwalya])
        promotions.place_held(self.head, held[0], self.c6a, "Met the parents; promote with support")
        self.assertEqual(self.bwalya.enrollments.get(academic_year=self.next).class_group, self.c6a)
        self.assertEqual(promotions.held_requests(), [])

    def test_final_grade_completes_and_leaving_transfers(self):
        finalist = f.register(self.admin, self.f4a, first="Mutale", last="Phiri", phone="0955000111",
                              guardian="Grace Phiri", dob=D(2009, 5, 5))
        leaver = f.register(self.admin, self.f4a, first="Kondwani", last="Lungu", phone="0977000222",
                            guardian="John Lungu", dob=D(2009, 8, 8))
        batch = self.prepare({leaver: (R.LEAVING, None, "Moving to Ndola")}, class_group=self.f4a, actor=self.admin)
        promotions.submit_sheet(self.admin, batch)
        promotions.approve_sheet(self.head, batch)
        finalist.refresh_from_db()
        leaver.refresh_from_db()
        self.assertEqual((finalist.status, leaver.status), (Learner.COMPLETED, Learner.TRANSFERRED))
        self.assertEqual(finalist.enrollments.get().end_reason, "completion")


class DatabaseRules(PromotionTestCase):
    def test_one_batch_per_class(self):
        self.prepare()
        with self.assertRaises(IntegrityError):
            PromotionBatch.objects.create(school=f.school(), from_class=self.c5a, from_year=self.year, to_year=self.next,
                                          prepared_by=self.teacher)

    def test_promote_needs_a_destination(self):
        batch = self.prepare()
        late = f.register(self.admin, self.c5b, first="Late", last="Joiner", phone="0955333444", guardian="X Y",
                          dob=D(2016, 2, 2))
        with self.assertRaises(IntegrityError):
            R.objects.create(school=f.school(), kind=R.PROMOTION, batch=batch, learner=late,
                             from_enrollment=late.enrollments.get(), decision=R.PROMOTE, effective_date=D(2027, 1, 1),
                             requested_by=self.teacher)


class Transfers(PromotionTestCase):
    def test_teacher_requests_headteacher_approves(self):
        req = promotions.request_transfer(self.teacher, self.anna, self.c5b, D(2026, 11, 23), "Friendship issues")
        self.assertEqual(req.status, "pending")
        self.assertEqual(self.anna.current_enrollment().class_group, self.c5a)  # nothing changes yet
        with self.assertRaisesMessage(ValidationError, "already waiting"):
            promotions.request_transfer(self.teacher, self.anna, self.c5b, D(2026, 11, 24), "again")
        promotions.approve_transfer(self.head, req)
        new = self.anna.current_enrollment()
        self.assertEqual((new.class_group, new.start_date, new.request_id), (self.c5b, D(2026, 11, 23), req.pk))

    def test_headteacher_request_takes_effect_at_once(self):
        req = promotions.request_transfer(self.head, self.bwalya, self.c5b, D(2026, 11, 23), "Class sizes")
        self.assertEqual(req.status, "approved")
        self.assertEqual(self.bwalya.current_enrollment().class_group, self.c5b)

    def test_reject_needs_a_note_and_strangers_cannot_request(self):
        req = promotions.request_transfer(self.teacher, self.anna, self.c5b, D(2026, 11, 23), "x")
        with self.assertRaises(ValidationError):
            promotions.reject_transfer(self.head, req, "")
        promotions.reject_transfer(self.head, req, "Class 5B is full")
        stranger = f.make_user("stranger", roles.TEACHER)
        with self.assertRaises(PermissionDenied):
            promotions.request_transfer(stranger, self.anna, self.c5b, D(2026, 11, 23), "x")


class Screens(PromotionTestCase):
    def test_teacher_prepares_and_submits_through_the_sheet(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get("/"), "promotion sheet for 5A to submit")
        self.assertContains(self.client.get("/promotions/"), "5A")
        page = self.client.get(f"/promotions/class/{self.c5a.pk}/")
        self.assertContains(page, "Submit to the Headteacher")
        data = {"action": "submit"}
        for learner in (self.anna, self.bwalya):
            data.update({f"decision_{learner.pk}": "promote", f"class_{learner.pk}": self.c6a.pk, f"note_{learner.pk}": ""})
        self.assertRedirects(self.client.post(f"/promotions/class/{self.c5a.pk}/", data), "/promotions/")
        self.assertEqual(PromotionBatch.objects.get().status, "submitted")

    def test_headteacher_reviews_with_confirmation(self):
        batch = self.prepare()
        promotions.submit_sheet(self.teacher, batch)
        self.client.force_login(self.head)
        self.assertContains(self.client.get("/"), "promotion sheets to approve")
        self.assertContains(self.client.get("/promotions/approvals/"), "5A")
        url = f"/promotions/approvals/sheet/{batch.pk}/"
        self.assertContains(self.client.get(url), "promoted to 6A")
        self.assertContains(self.client.post(url, {"action": "approve"}), "Tick the box")
        self.assertRedirects(self.client.post(url, {"action": "approve", "confirm": "yes"}), "/promotions/approvals/")
        batch.refresh_from_db()
        self.assertEqual(batch.status, "approved")

    def test_transfer_page_sends_a_request(self):
        self.client.force_login(self.teacher)
        response = self.client.post(f"/learners/{self.anna.pk}/transfer/", {
            "to_class": self.c5b.pk, "effective_date": "2026-11-23", "reason": "Closer to her sister"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(R.objects.get().status, "pending")
        self.assertContains(self.client.get(f"/learners/{self.anna.pk}/?tab=enrollments"), "Promotion and transfer requests")
