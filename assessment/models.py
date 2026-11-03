"""Grading schemes, assessments, marks and competencies (slice 3 design, docs/database-design-assessment.md)."""
from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models
from django.db.models import F, Func, Q, Value
from django.contrib.postgres.fields import DecimalRangeField

from core.db import choices, status_check
from core.models import School
from learners.models import Enrollment, Learner
from school.models import AcademicYear, ClassGroup, ClassSubject, EducationStage, Grade, Term

VERSION_STATUSES = ["draft", "active", "retired"]
MISSING_STATUSES = ["absent", "excused", "not_yet_assessed"]


class NumRange(Func):
    function = "numrange"
    output_field = DecimalRangeField()


class GradingScheme(models.Model):
    """Turns percentages into grades for one stage. An active scheme is never edited: changes are a new version."""

    DRAFT, ACTIVE, RETIRED = VERSION_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="grading_schemes")
    name = models.TextField()
    version = models.SmallIntegerField(default=1)
    effective_from_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.TextField(choices=choices(*VERSION_STATUSES), default=DRAFT)
    decimal_places = models.SmallIntegerField(default=0, help_text="Rounding of percentages (half up).")
    missing_rule = models.TextField(
        choices=[("exclude_excused", "Leave out Excused if the component has another result"),
                 ("block", "Any missing result leaves the subject pending")],
        default="exclude_excused",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grading_schemes"
        ordering = ["stage__sequence", "name", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["school", "stage", "name", "version"], name="grading_schemes_version_unique"),
            status_check("grading_schemes_status_valid", "status", VERSION_STATUSES),
            status_check("grading_schemes_missing_rule_valid", "missing_rule", ["exclude_excused", "block"]),
            models.CheckConstraint(condition=Q(decimal_places__gte=0, decimal_places__lte=2), name="grading_schemes_decimals_0_2"),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.stage})"

    def band_for(self, percent):
        for band in self.bands.all():
            if band.min_percent <= percent <= band.max_percent:
                return band
        return None


class GradeBand(models.Model):
    scheme = models.ForeignKey(GradingScheme, on_delete=models.PROTECT, related_name="bands")
    label = models.TextField(help_text="e.g. Distinction, 1, A")
    description = models.TextField(blank=True)
    min_percent = models.DecimalField(max_digits=5, decimal_places=2)
    max_percent = models.DecimalField(max_digits=5, decimal_places=2)
    points = models.SmallIntegerField(null=True, blank=True)
    is_pass = models.BooleanField(default=True)

    class Meta:
        db_table = "grade_bands"
        ordering = ["scheme", "-min_percent"]
        constraints = [
            models.CheckConstraint(
                condition=Q(min_percent__gte=0, max_percent__lte=100, min_percent__lte=F("max_percent")),
                name="grade_bands_range_valid",
            ),
            ExclusionConstraint(
                name="grade_bands_no_overlap",
                expressions=[
                    ("scheme", RangeOperators.EQUAL),
                    (NumRange(F("min_percent"), F("max_percent"), Value("[]")), RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return f"{self.label} ({self.min_percent}–{self.max_percent})"


class AssessmentType(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT)
    name = models.TextField()
    status = models.TextField(choices=choices("active", "retired"), default="active")

    class Meta:
        db_table = "assessment_types"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="assessment_types_name_unique"),
            status_check("assessment_types_status_valid", "status", ["active", "retired"]),
        ]

    def __str__(self):
        return self.name


class AssessmentPlan(models.Model):
    """Components, weights and grading scheme for a stage, or one grade, in a term."""

    DRAFT, ACTIVE = "draft", "active"

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="+")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="assessment_plans")
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="+")
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                              help_text="Leave blank to cover the whole stage; a grade plan overrides the stage plan.")
    grading_scheme = models.ForeignKey(GradingScheme, on_delete=models.PROTECT, related_name="plans")
    status = models.TextField(choices=choices(DRAFT, ACTIVE), default=DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assessment_plans"
        ordering = ["term", "stage__sequence", "grade__sequence"]
        constraints = [
            status_check("assessment_plans_status_valid", "status", ["draft", "active"]),
            models.UniqueConstraint(fields=["term", "stage"], condition=Q(grade__isnull=True), name="assessment_plans_one_per_stage"),
            models.UniqueConstraint(fields=["term", "grade"], condition=Q(grade__isnull=False), name="assessment_plans_one_per_grade"),
        ]

    def __str__(self):
        return f"{self.grade or self.stage}, {self.term}"


class AssessmentComponent(models.Model):
    plan = models.ForeignKey(AssessmentPlan, on_delete=models.PROTECT, related_name="components")
    name = models.TextField(help_text="e.g. Continuous assessment, End-of-term exam")
    weight = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                 help_text="Percent of the subject score. All weights must add up to 100 before activation.")
    required = models.BooleanField(default=True, help_text="A required component with no usable result leaves the subject pending.")
    sequence = models.SmallIntegerField(default=1)

    class Meta:
        db_table = "assessment_components"
        ordering = ["plan", "sequence", "name"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "name"], name="assessment_components_name_unique"),
            models.CheckConstraint(condition=Q(weight__isnull=True) | Q(weight__gt=0, weight__lte=100),
                                   name="assessment_components_weight_valid"),
        ]

    def __str__(self):
        return f"{self.name} ({self.weight if self.weight is not None else '—'}%)"


class Assessment(models.Model):
    DRAFT, SUBMITTED = "draft", "submitted"

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name="assessments")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="assessments")
    component = models.ForeignKey(AssessmentComponent, on_delete=models.PROTECT, related_name="assessments")
    assessment_type = models.ForeignKey(AssessmentType, on_delete=models.PROTECT, related_name="+")
    title = models.TextField()
    max_score = models.DecimalField(max_digits=6, decimal_places=2)
    assessed_on = models.DateField()
    status = models.TextField(choices=choices(DRAFT, SUBMITTED), default=DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessments"
        ordering = ["term", "assessed_on", "title"]
        permissions = [
            ("enter_marks", "Can create assessments and enter marks for class subjects they teach"),
            ("correct_marks", "Can correct any submitted marks, with a reason"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["class_subject", "term", "title"], name="assessments_title_unique"),
            models.UniqueConstraint(fields=["id", "class_subject"], name="assessments_id_class_subject_unique"),
            models.CheckConstraint(condition=Q(max_score__gt=0), name="assessments_max_score_positive"),
            status_check("assessments_status_valid", "status", ["draft", "submitted"]),
            models.CheckConstraint(
                condition=Q(status="submitted", submitted_by__isnull=False, submitted_at__isnull=False)
                | Q(status="draft", submitted_by__isnull=True, submitted_at__isnull=True),
                name="assessments_submission_complete",
            ),
        ]

    def __str__(self):
        return f"{self.class_subject} — {self.title}"


class AssessmentResult(models.Model):
    ABSENT, EXCUSED, NOT_YET_ASSESSED = MISSING_STATUSES

    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="results")
    # class_subject_id, class_id, enrollment_id and learner_id are tied together by composite keys (migration 0002).
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name="+")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="+", db_column="class_id")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="assessment_results")
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="assessment_results")
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    missing_status = models.TextField(choices=choices(*MISSING_STATUSES), blank=True)
    comment = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_results"
        constraints = [
            models.UniqueConstraint(fields=["assessment", "learner"], name="assessment_results_one_per_learner"),
            models.CheckConstraint(condition=Q(score__isnull=True) | Q(score__gte=0), name="assessment_results_score_not_negative"),
            models.CheckConstraint(condition=Q(missing_status="") | Q(missing_status__in=MISSING_STATUSES),
                                   name="assessment_results_missing_valid"),
            # A mark or a missing status, never both. A draft row may have neither; submission requires one.
            models.CheckConstraint(condition=Q(score__isnull=True) | Q(missing_status=""), name="assessment_results_not_both"),
        ]

    def __str__(self):
        return f"{self.learner} {self.score if self.score is not None else self.missing_status or '—'}"

    @property
    def is_complete(self):
        return self.score is not None or bool(self.missing_status)


class AssessmentResultHistory(models.Model):
    """Every change after submission. Append-only (trigger)."""

    result = models.ForeignKey(AssessmentResult, on_delete=models.PROTECT, related_name="history")
    old_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    new_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    old_missing_status = models.TextField(blank=True)
    new_missing_status = models.TextField(blank=True)
    old_comment = models.TextField(blank=True)
    new_comment = models.TextField(blank=True)
    reason = models.TextField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assessment_result_history"
        ordering = ["changed_at", "id"]
        constraints = [models.CheckConstraint(condition=~Q(reason=""), name="assessment_result_history_reason")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Result history cannot be changed.")
        super().save(*args, **kwargs)


class CompetencyFramework(models.Model):
    DRAFT, ACTIVE, RETIRED = VERSION_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    stage = models.ForeignKey(EducationStage, on_delete=models.PROTECT, related_name="competency_frameworks")
    name = models.TextField()
    version = models.SmallIntegerField(default=1)
    effective_from_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.TextField(choices=choices(*VERSION_STATUSES), default=DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "competency_frameworks"
        ordering = ["stage__sequence", "name", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["school", "stage", "name", "version"], name="competency_frameworks_version_unique"),
            status_check("competency_frameworks_status_valid", "status", VERSION_STATUSES),
            models.UniqueConstraint(fields=["school", "stage"], condition=Q(status="active"),
                                    name="competency_frameworks_one_active_per_stage"),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.stage})"


class Competency(models.Model):
    framework = models.ForeignKey(CompetencyFramework, on_delete=models.PROTECT, related_name="competencies")
    code = models.TextField()
    name = models.TextField()
    description = models.TextField(blank=True)
    sequence = models.SmallIntegerField(default=1)

    class Meta:
        db_table = "competencies"
        verbose_name_plural = "competencies"
        ordering = ["framework", "sequence", "name"]
        constraints = [models.UniqueConstraint(fields=["framework", "code"], name="competencies_code_unique")]

    def __str__(self):
        return self.name


class CompetencyLevel(models.Model):
    framework = models.ForeignKey(CompetencyFramework, on_delete=models.PROTECT, related_name="levels")
    code = models.TextField()
    label = models.TextField()
    description = models.TextField(blank=True)
    sequence = models.SmallIntegerField(help_text="1 = lowest")

    class Meta:
        db_table = "competency_levels"
        ordering = ["framework", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["framework", "sequence"], name="competency_levels_sequence_unique"),
            models.UniqueConstraint(fields=["framework", "code"], name="competency_levels_code_unique"),
        ]

    def __str__(self):
        return self.label


class CompetencySheet(models.Model):
    """One class's end-of-term competency record (cross-cutting, decision B6)."""

    DRAFT, SUBMITTED = "draft", "submitted"

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="competency_sheets", db_column="class_id")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="competency_sheets")
    framework = models.ForeignKey(CompetencyFramework, on_delete=models.PROTECT, related_name="sheets")
    status = models.TextField(choices=choices(DRAFT, SUBMITTED), default=DRAFT)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "competency_sheets"
        permissions = [("record_competencies", "Can record end-of-term competencies for a class they are class teacher of")]
        constraints = [
            models.UniqueConstraint(fields=["class_group", "term"], name="competency_sheets_one_per_class_term"),
            models.UniqueConstraint(fields=["id", "class_group"], name="competency_sheets_id_class_unique"),
            status_check("competency_sheets_status_valid", "status", ["draft", "submitted"]),
            models.CheckConstraint(
                condition=Q(status="submitted", submitted_by__isnull=False, submitted_at__isnull=False)
                | Q(status="draft", submitted_by__isnull=True, submitted_at__isnull=True),
                name="competency_sheets_submission_complete",
            ),
        ]

    def __str__(self):
        return f"{self.class_group.name} competencies, {self.term}"


class LearnerCompetencyResult(models.Model):
    sheet = models.ForeignKey(CompetencySheet, on_delete=models.PROTECT, related_name="results")
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT, related_name="+")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="+", db_column="class_id")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="competency_results")
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="competency_results")
    level = models.ForeignKey(CompetencyLevel, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    not_assessed = models.BooleanField(default=False)
    comment = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learner_competency_results"
        constraints = [
            models.UniqueConstraint(fields=["sheet", "learner", "competency"], name="competency_results_one_per_cell"),
            # Not Assessed is a status, not the lowest level: never both.
            models.CheckConstraint(condition=Q(level__isnull=True) | Q(not_assessed=False), name="competency_results_not_both"),
        ]

    @property
    def is_complete(self):
        return self.level_id is not None or self.not_assessed
