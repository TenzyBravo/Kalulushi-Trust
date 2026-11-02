"""School and academic structure (design §4)."""
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from core.fields import period, status_check


SCHOOL_STATUSES = [("active", "Active"), ("inactive", "Inactive")]


class School(models.Model):
    STATUSES = SCHOOL_STATUSES

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=10, unique=True, help_text="Short code, e.g. KLT. Prefixes learner numbers.")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "schools"
        constraints = [status_check("schools_status_valid", SCHOOL_STATUSES)]

    def __str__(self):
        return self.name

    @classmethod
    def current(cls):
        """The school this installation serves. Version 1 has exactly one."""
        try:
            return cls.objects.get(status="active")
        except cls.DoesNotExist:
            raise cls.DoesNotExist("No school is set up yet. Run: manage.py setup_school") from None


class SchoolSettings(models.Model):
    school = models.OneToOneField(School, on_delete=models.PROTECT, primary_key=True, related_name="settings")
    sms_sender_name = models.CharField(max_length=11, blank=True, help_text="Up to 11 characters.")
    logo = models.FileField(upload_to="logos/", blank=True)
    motto = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "school_settings"
        verbose_name_plural = "school settings"

    def __str__(self):
        return f"Settings for {self.school}"


class EducationStage(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="stages")
    name = models.CharField(max_length=50, help_text="e.g. Primary, Secondary")
    sequence = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "education_stages"
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="education_stages_unique_name")]

    def __str__(self):
        return self.name


GRADE_STATUSES = [("active", "Active"), ("retired", "Retired")]


class Grade(models.Model):
    STATUSES = GRADE_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="grades")
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="grades")
    name = models.CharField(max_length=50, help_text="e.g. Grade 5, Form 2")
    code = models.CharField(max_length=10, help_text="Used in class names, e.g. 5 or F2")
    sequence = models.PositiveSmallIntegerField(help_text="Promotion order across the school")
    status = models.CharField(max_length=10, choices=STATUSES, default="active")

    class Meta:
        db_table = "grades"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="grades_unique_code"),
            models.UniqueConstraint(fields=["school", "sequence"], name="grades_unique_sequence"),
            status_check("grades_status_valid", GRADE_STATUSES),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.stage_id and self.school_id and self.stage.school_id != self.school_id:
            raise ValidationError({"stage": "This stage belongs to another school."})


ACADEMIC_YEAR_STATUSES = [("planned", "Planned"), ("current", "Current"), ("closed", "Closed")]


class AcademicYear(models.Model):
    STATUSES = ACADEMIC_YEAR_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="academic_years")
    name = models.CharField(max_length=20, help_text="e.g. 2027")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUSES, default="planned")

    class Meta:
        db_table = "academic_years"
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="academic_years_unique_name"),
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")),
                name="academic_years_dates_ordered",
                violation_error_message="The end date must be after the start date.",
            ),
            models.UniqueConstraint(
                fields=["school"],
                condition=Q(status="current"),
                name="academic_years_one_current",
                violation_error_message="Another academic year is already current.",
            ),
            ExclusionConstraint(
                name="academic_years_no_overlap",
                expressions=[("school", RangeOperators.EQUAL), (period(), RangeOperators.OVERLAPS)],
                violation_error_message="These dates overlap another academic year.",
            ),
            status_check("academic_years_status_valid", ACADEMIC_YEAR_STATUSES),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def current_for(cls, school):
        return cls.objects.filter(school=school, status="current").first()


class Term(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="terms")
    number = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "terms"
        ordering = ["academic_year__start_date", "number"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "number"], name="terms_unique_number"),
            models.CheckConstraint(condition=Q(number__gte=1, number__lte=3), name="terms_number_1_to_3"),
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")),
                name="terms_dates_ordered",
                violation_error_message="The end date must be after the start date.",
            ),
            ExclusionConstraint(
                name="terms_no_overlap",
                expressions=[("academic_year", RangeOperators.EQUAL), (period(), RangeOperators.OVERLAPS)],
                violation_error_message="These dates overlap another term in this year.",
            ),
        ]

    def __str__(self):
        return f"Term {self.number}, {self.academic_year}"

    def clean(self):
        # Spans two tables, so the database cannot check it (design §4, terms).
        if self.academic_year_id and self.start_date and self.end_date:
            year = self.academic_year
            if self.start_date < year.start_date or self.end_date > year.end_date:
                raise ValidationError(
                    f"Term dates must fall within the academic year ({year.start_date} to {year.end_date})."
                )


SCHOOL_CLASS_STATUSES = [("active", "Active"), ("closed", "Closed")]


class SchoolClass(models.Model):
    """A class for one academic year: 5A in 2026 and 5A in 2027 are two rows (decision D1)."""

    STATUSES = SCHOOL_CLASS_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="classes")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="classes")
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="classes")
    stream = models.CharField(max_length=10, blank=True, help_text="e.g. A. Leave blank for a single class.")
    name = models.CharField(max_length=20, editable=False)
    status = models.CharField(max_length=10, choices=STATUSES, default="active")

    class Meta:
        db_table = "classes"
        verbose_name = "class"
        verbose_name_plural = "classes"
        ordering = ["-academic_year__start_date", "grade__sequence", "stream"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "grade", "stream"],
                name="classes_unique_per_year",
                violation_error_message="This class already exists for this year.",
            ),
            # Target of the composite foreign key from enrollments (design §6).
            models.UniqueConstraint(fields=["id", "academic_year"], name="classes_unique_id_year"),
            status_check("classes_status_valid", SCHOOL_CLASS_STATUSES),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    def clean(self):
        for name in ("academic_year", "grade"):
            related = getattr(self, name, None) if getattr(self, f"{name}_id") else None
            if related and self.school_id and related.school_id != self.school_id:
                raise ValidationError({name: "This belongs to another school."})

    def save(self, *args, **kwargs):
        self.stream = self.stream.strip().upper()
        self.name = f"{self.grade.code}{self.stream}"
        super().save(*args, **kwargs)

    @property
    def stage(self):
        return self.grade.stage


SUBJECT_STATUSES = [("active", "Active"), ("retired", "Retired")]


class Subject(models.Model):
    STATUSES = SUBJECT_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="subjects")
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="subjects")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=STATUSES, default="active")

    class Meta:
        db_table = "subjects"
        ordering = ["stage__sequence", "name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="subjects_unique_code"),
            status_check("subjects_status_valid", SUBJECT_STATUSES),
        ]

    def __str__(self):
        return f"{self.name} ({self.stage})"


CLASS_SUBJECT_STATUSES = [("active", "Active"), ("dropped", "Dropped")]


class ClassSubject(models.Model):
    """A subject a class takes this year. Teacher access is granted on this unit."""

    STATUSES = CLASS_SUBJECT_STATUSES

    school_class = models.ForeignKey(SchoolClass, on_delete=models.PROTECT, related_name="class_subjects")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="class_subjects")
    status = models.CharField(max_length=10, choices=STATUSES, default="active")

    class Meta:
        db_table = "class_subjects"
        ordering = ["school_class", "subject__name"]
        constraints = [
            models.UniqueConstraint(fields=["school_class", "subject"], name="class_subjects_unique"),
            status_check("class_subjects_status_valid", CLASS_SUBJECT_STATUSES),
        ]

    def __str__(self):
        return f"{self.school_class.name} {self.subject.name} ({self.school_class.academic_year})"

    def clean(self):
        if self.school_class_id and self.subject_id:
            if self.subject.stage_id != self.school_class.grade.stage_id:
                raise ValidationError(
                    f"{self.subject.name} is a {self.subject.stage} subject; "
                    f"{self.school_class.name} is a {self.school_class.grade.stage} class."
                )
