from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class SchoolSettings(models.Model):
    """The school's own details. There is exactly one row; use SchoolSettings.load()."""

    name = models.CharField(max_length=200, default="Our School")
    motto = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    sms_sender_name = models.CharField(
        max_length=11, blank=True, help_text="Up to 11 characters, shown as the SMS sender."
    )

    class Meta:
        verbose_name = "school settings"
        verbose_name_plural = "school settings"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("School settings cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AcademicYear(models.Model):
    name = models.CharField(max_length=20, unique=True, help_text="e.g. 2026")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"], condition=Q(is_current=True), name="one_current_academic_year"
            ),
            models.CheckConstraint(
                condition=Q(end_date__gt=models.F("start_date")), name="academic_year_dates_ordered"
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})

    def save(self, *args, **kwargs):
        if self.is_current:
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        return cls.objects.filter(is_current=True).first()


class Term(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["academic_year__start_date", "number"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "number"], name="unique_term_per_year"),
        ]

    def __str__(self):
        return f"Term {self.number}, {self.academic_year}"

    def clean(self):
        if not (self.start_date and self.end_date):
            return
        if self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})
        year = self.academic_year if self.academic_year_id else None
        if year and (self.start_date < year.start_date or self.end_date > year.end_date):
            raise ValidationError("Term dates must fall within the academic year.")


class Level(models.Model):
    """A section of the school, e.g. Primary or Secondary.

    A primary-only, secondary-only or combined school is just a different set of rows.
    """

    name = models.CharField(max_length=50, unique=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Grade(models.Model):
    """A year group within a level. Names are data, so Grade 8 or Form 1 both work."""

    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="grades")
    name = models.CharField(max_length=50, help_text="e.g. Grade 4 or Form 1")
    code = models.CharField(max_length=10, help_text="Short code used in class names, e.g. 4")
    order = models.PositiveSmallIntegerField(default=0, help_text="Promotion order across the school")

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="unique_grade_code"),
            models.UniqueConstraint(fields=["level", "name"], name="unique_grade_name_per_level"),
        ]

    def __str__(self):
        return self.name


class ClassGroup(models.Model):
    """A class of pupils for one year, e.g. 4A in 2026."""

    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="class_groups")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="class_groups")
    stream = models.CharField(max_length=10, blank=True, help_text="e.g. A. Leave blank for a single class.")
    class_teacher = models.ForeignKey(
        "people.Staff", on_delete=models.SET_NULL, null=True, blank=True, related_name="classes_led"
    )

    class Meta:
        ordering = ["academic_year__start_date", "grade__order", "stream"]
        constraints = [
            models.UniqueConstraint(fields=["grade", "academic_year", "stream"], name="unique_class_per_year"),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    @property
    def name(self):
        return f"{self.grade.code}{self.stream}"

    @property
    def level(self):
        return self.grade.level


class Subject(models.Model):
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="subjects")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["level__order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["level", "code"], name="unique_subject_code_per_level"),
        ]

    def __str__(self):
        return f"{self.name} ({self.level})"
