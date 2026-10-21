from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from people.models import Staff, Student
from school.models import AcademicYear, ClassGroup, Level, Subject

PERCENT = [MinValueValidator(0), MaxValueValidator(100)]


class GradingScale(models.Model):
    """How marks turn into grades for one level, e.g. the ECZ 1–9 scale for secondary."""

    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="grading_scales")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["level__order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["level"], condition=Q(is_active=True), name="one_active_scale_per_level"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.level})"

    def band_for(self, score):
        return self.bands.filter(min_score__lte=score, max_score__gte=score).first()

    def check_bands(self):
        """Return a list of problems: overlaps and gaps across 0–100."""
        problems = []
        bands = list(self.bands.order_by("min_score"))
        expected = 0
        for band in bands:
            if band.min_score > expected:
                problems.append(f"No band covers {expected}–{band.min_score - 1}")
            elif band.min_score < expected:
                problems.append(f"Band {band.label} overlaps the band below it")
            expected = max(expected, band.max_score + 1)
        if bands and expected <= 100:
            problems.append(f"No band covers {expected}–100")
        return problems


class GradeBand(models.Model):
    scale = models.ForeignKey(GradingScale, on_delete=models.CASCADE, related_name="bands")
    label = models.CharField(max_length=10, help_text="e.g. 1 or A")
    description = models.CharField(max_length=50, blank=True, help_text="e.g. Distinction")
    min_score = models.PositiveSmallIntegerField(validators=PERCENT)
    max_score = models.PositiveSmallIntegerField(validators=PERCENT)
    is_pass = models.BooleanField(default=True)

    class Meta:
        ordering = ["scale", "-min_score"]
        constraints = [
            models.UniqueConstraint(fields=["scale", "label"], name="unique_band_label_per_scale"),
            models.CheckConstraint(condition=Q(min_score__lte=F("max_score")), name="band_min_le_max"),
            models.CheckConstraint(condition=Q(max_score__lte=100), name="band_max_le_100"),
        ]

    def __str__(self):
        return f"{self.label} ({self.min_score}–{self.max_score})"

    def clean(self):
        if self.min_score is None or self.max_score is None:
            return
        if self.min_score > self.max_score:
            raise ValidationError("Minimum score must not be above the maximum score.")
        if self.scale_id:
            overlapping = self.scale.bands.exclude(pk=self.pk).filter(
                min_score__lte=self.max_score, max_score__gte=self.min_score
            )
            if overlapping.exists():
                raise ValidationError(f"Overlaps band {overlapping.first().label}.")


class Enrollment(models.Model):
    """A pupil's class for one academic year. History is kept across promotions."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="enrollments")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="enrollments")

    class Meta:
        ordering = ["-academic_year__start_date"]
        verbose_name = "enrolment"
        constraints = [
            models.UniqueConstraint(fields=["student", "academic_year"], name="one_class_per_student_per_year"),
        ]

    def __str__(self):
        return f"{self.student.full_name} in {self.class_group}"

    def clean(self):
        if self.class_group_id and self.academic_year_id:
            if self.class_group.academic_year_id != self.academic_year_id:
                raise ValidationError("The class belongs to a different academic year.")

    def save(self, *args, **kwargs):
        if self.class_group_id and not self.academic_year_id:
            self.academic_year_id = self.class_group.academic_year_id
        self.clean()
        super().save(*args, **kwargs)


class TeachingAssignment(models.Model):
    """Who teaches which subject to which class."""

    teacher = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="teaching_assignments")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="teaching_assignments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="teaching_assignments")

    class Meta:
        ordering = ["class_group", "subject"]
        constraints = [
            models.UniqueConstraint(fields=["class_group", "subject", "teacher"], name="unique_teaching_assignment"),
        ]

    def __str__(self):
        return f"{self.teacher} – {self.subject.name} – {self.class_group.name}"

    def clean(self):
        if self.subject_id and self.class_group_id:
            if self.subject.level_id != self.class_group.grade.level_id:
                raise ValidationError("This subject is not taught at this class's level.")
