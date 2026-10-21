from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from academics.models import Enrollment, GradeBand, GradingScale, TeachingAssignment
from school.models import ClassGroup

from .factories import make_staff, make_structure, make_student, make_subject, make_year


class GradingScaleTests(TestCase):
    def setUp(self):
        self.s = make_structure()
        self.scale = GradingScale.objects.create(level=self.s["secondary"], name="Test")

    def test_overlapping_band_rejected(self):
        GradeBand.objects.create(scale=self.scale, label="1", min_score=75, max_score=100)
        band = GradeBand(scale=self.scale, label="2", min_score=70, max_score=80)
        with self.assertRaises(ValidationError):
            band.full_clean()

    def test_gaps_reported(self):
        GradeBand.objects.create(scale=self.scale, label="1", min_score=50, max_score=100)
        self.assertEqual(self.scale.check_bands(), ["No band covers 0–49"])

    def test_band_lookup(self):
        GradeBand.objects.create(scale=self.scale, label="P", min_score=40, max_score=100)
        GradeBand.objects.create(scale=self.scale, label="F", min_score=0, max_score=39, is_pass=False)
        self.assertEqual(self.scale.band_for(39).label, "F")
        self.assertEqual(self.scale.band_for(40).label, "P")


class EnrollmentTests(TestCase):
    def setUp(self):
        self.s = make_structure()
        self.pupil = make_student()

    def test_one_class_per_year_with_history(self):
        Enrollment.objects.create(student=self.pupil, class_group=self.s["classes"]["4A"])
        with self.assertRaises(IntegrityError):
            Enrollment.objects.create(student=self.pupil, class_group=self.s["classes"]["2A"])

    def test_promotion_keeps_history(self):
        Enrollment.objects.create(student=self.pupil, class_group=self.s["classes"]["4A"])
        y2027 = make_year("2027")
        c5 = ClassGroup.objects.create(grade=self.s["grades"][5], academic_year=y2027, stream="A")
        Enrollment.objects.create(student=self.pupil, class_group=c5)
        self.assertEqual(self.pupil.enrollments.count(), 2)

    def test_class_must_match_year(self):
        y2027 = make_year("2027", current=False)
        with self.assertRaises(ValidationError):
            Enrollment.objects.create(student=self.pupil, academic_year=y2027, class_group=self.s["classes"]["4A"])


class TeachingAssignmentTests(TestCase):
    def test_subject_must_match_class_level(self):
        s = make_structure()
        maths_secondary = make_subject(s["secondary"])
        ta = TeachingAssignment(teacher=make_staff(), class_group=s["classes"]["4A"], subject=maths_secondary)
        with self.assertRaises(ValidationError):
            ta.full_clean()
