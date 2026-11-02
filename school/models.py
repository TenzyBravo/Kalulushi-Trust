from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q, Value

from core.db import DateRange, choices, status_check
from core.models import School


class EducationStage(models.Model):
    """Primary and Secondary are never compared directly (baseline §2, §11)."""

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="stages")
    name = models.TextField()
    sequence = models.SmallIntegerField(default=0)

    class Meta:
        db_table = "education_stages"
        ordering = ["sequence", "name"]
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="stages_name_unique")]

    def __str__(self):
        return self.name


class Grade(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="grades")
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="grades")
    name = models.TextField(help_text="e.g. Grade 5 or Form 2")
    code = models.TextField(help_text="Used in class names: 1 … 7, F1 … F4")
    sequence = models.SmallIntegerField(help_text="Promotion order across the school, 1–11")
    status = models.TextField(choices=choices("active", "retired"), default="active")

    class Meta:
        db_table = "grades"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="grades_code_unique"),
            models.UniqueConstraint(fields=["school", "sequence"], name="grades_sequence_unique"),
            status_check("grades_status_valid", "status", ["active", "retired"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.stage_id and self.school_id and self.stage.school_id != self.school_id:
            raise ValidationError({"stage": "This stage belongs to another school."})


class AcademicYear(models.Model):
    PLANNED, CURRENT, CLOSED = "planned", "current", "closed"

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="academic_years")
    name = models.TextField(help_text="e.g. 2027")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.TextField(choices=choices(PLANNED, CURRENT, CLOSED), default=PLANNED)

    class Meta:
        db_table = "academic_years"
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="years_name_unique"),
            models.UniqueConstraint(fields=["school"], condition=Q(status="current"), name="years_one_current"),
            models.CheckConstraint(condition=Q(end_date__gt=F("start_date")), name="years_dates_ordered"),
            status_check("years_status_valid", "status", ["planned", "current", "closed"]),
            ExclusionConstraint(
                name="years_no_overlap",
                expressions=[
                    ("school", RangeOperators.EQUAL),
                    (DateRange(F("start_date"), F("end_date"), Value("[]")), RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})

    def contains(self, day):
        return self.start_date <= day <= self.end_date

    @classmethod
    def current(cls, school=None):
        return cls.objects.filter(school=school or School.current(), status=cls.CURRENT).first()


class Term(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="terms")
    number = models.SmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "terms"
        ordering = ["academic_year__start_date", "number"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "number"], name="terms_number_unique"),
            models.CheckConstraint(condition=Q(number__gte=1, number__lte=3), name="terms_number_1_to_3"),
            models.CheckConstraint(condition=Q(end_date__gt=F("start_date")), name="terms_dates_ordered"),
            ExclusionConstraint(
                name="terms_no_overlap",
                expressions=[
                    ("academic_year", RangeOperators.EQUAL),
                    (DateRange(F("start_date"), F("end_date"), Value("[]")), RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return f"Term {self.number}, {self.academic_year}"

    def clean(self):
        # Spans two tables, so it is checked here rather than in the database (design §4).
        if not (self.start_date and self.end_date):
            return
        if self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after the start date."})
        year = self.academic_year if self.academic_year_id else None
        if year and not (year.contains(self.start_date) and year.contains(self.end_date)):
            raise ValidationError("Term dates must fall inside the academic year.")


class ClassGroup(models.Model):
    """A class for one academic year: 5A in 2026 and 5A in 2027 are two rows (D1)."""

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="classes")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="classes")
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="classes")
    stream = models.TextField(blank=True, help_text="e.g. A. Leave blank for a single class.")
    name = models.TextField(editable=False)
    status = models.TextField(choices=choices("active", "closed"), default="active")

    class Meta:
        db_table = "classes"
        verbose_name = "class"
        verbose_name_plural = "classes"
        ordering = ["academic_year__start_date", "grade__sequence", "stream"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "grade", "stream"], name="classes_unique_per_year"),
            # Target of the composite foreign key from enrollments.
            models.UniqueConstraint(fields=["id", "academic_year"], name="classes_id_year_unique"),
            status_check("classes_status_valid", "status", ["active", "closed"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    def save(self, *args, **kwargs):
        self.stream = (self.stream or "").strip().upper()
        self.name = f"{self.grade.code}{self.stream}"
        super().save(*args, **kwargs)

    @property
    def stage(self):
        return self.grade.stage


class Subject(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="subjects")
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="subjects")
    name = models.TextField()
    code = models.TextField()
    status = models.TextField(choices=choices("active", "retired"), default="active")

    class Meta:
        db_table = "subjects"
        ordering = ["stage__sequence", "name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="subjects_code_unique"),
            status_check("subjects_status_valid", "status", ["active", "retired"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.stage})"


class ClassSubject(models.Model):
    """A subject a class takes this year: the unit teacher access is granted on."""

    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="class_subjects", db_column="class_id")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="class_subjects")
    status = models.TextField(choices=choices("active", "dropped"), default="active")

    class Meta:
        db_table = "class_subjects"
        ordering = ["class_group", "subject__name"]
        constraints = [
            models.UniqueConstraint(fields=["class_group", "subject"], name="class_subjects_unique"),
            status_check("class_subjects_status_valid", "status", ["active", "dropped"]),
        ]

    def __str__(self):
        return f"{self.class_group.name} {self.subject.name}"

    def clean(self):
        if self.class_group_id and self.subject_id and self.subject.stage_id != self.class_group.grade.stage_id:
            raise ValidationError("This subject is not taught at this class's stage.")
