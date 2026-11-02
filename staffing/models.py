from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, RangeOperators
from django.db import models
from django.db.models import F, Q, Value
from django.utils import timezone

from core.db import DateRange, choices, status_check
from core.models import School
from school.models import ClassGroup, ClassSubject

DUTIES = ["attendance", "assessment_entry", "competency_entry"]


class TeacherAssignment(models.Model):
    PRIMARY, BACKUP = "primary", "backup"

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="teacher_assignments")
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name="teacher_assignments")
    assignment_type = models.TextField(choices=choices(PRIMARY, BACKUP), default=PRIMARY)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.TextField(choices=choices("active", "ended"), default="active")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "teacher_assignments"
        ordering = ["class_subject", "assignment_type", "-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="ta_dates_ordered"
            ),
            status_check("ta_status_valid", "status", ["active", "ended"]),
            status_check("ta_type_valid", "assignment_type", ["primary", "backup"]),
            ExclusionConstraint(
                name="ta_one_primary_at_a_time",
                expressions=[
                    ("class_subject", RangeOperators.EQUAL),
                    (DateRange(F("start_date"), F("end_date"), Value("[]")), RangeOperators.OVERLAPS),
                ],
                condition=Q(assignment_type="primary", status="active"),
            ),
            ExclusionConstraint(
                name="ta_no_duplicate_teacher",
                expressions=[
                    ("class_subject", RangeOperators.EQUAL),
                    ("teacher", RangeOperators.EQUAL),
                    (DateRange(F("start_date"), F("end_date"), Value("[]")), RangeOperators.OVERLAPS),
                ],
                condition=Q(status="active"),
            ),
        ]

    def __str__(self):
        return f"{self.teacher} – {self.class_subject} ({self.assignment_type})"

    def is_open(self, day=None):
        day = day or timezone.localdate()
        return self.status == "active" and self.start_date <= day and (self.end_date is None or day <= self.end_date)


class ClassTeacherAssignment(models.Model):
    """The class teacher receives absence alerts (baseline §6)."""

    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="class_teachers", db_column="class_id")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="class_teacher_assignments")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "class_teacher_assignments"
        ordering = ["class_group", "-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="cta_dates_ordered"
            ),
            ExclusionConstraint(
                name="cta_one_class_teacher_at_a_time",
                expressions=[
                    ("class_group", RangeOperators.EQUAL),
                    (DateRange(F("start_date"), F("end_date"), Value("[]")), RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return f"{self.teacher} – class teacher of {self.class_group}"


class CoverAuthorization(models.Model):
    """Time-bound cover for a primary assignment. Expires on its own; no job switches it off."""

    teacher_assignment = models.ForeignKey(TeacherAssignment, on_delete=models.PROTECT, related_name="covers")
    cover_teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="covers")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    duties = ArrayField(models.TextField(choices=choices(*DUTIES)))
    reason = models.TextField()
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cover_authorizations"
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="cover_window_ordered"),
            models.CheckConstraint(
                condition=Q(duties__contained_by=DUTIES, duties__len__gt=0), name="cover_duties_valid"
            ),
            models.CheckConstraint(condition=~Q(reason=""), name="cover_reason_required"),
        ]

    def __str__(self):
        return f"{self.cover_teacher} covering {self.teacher_assignment.class_subject}"

    def is_live(self, at=None):
        at = at or timezone.now()
        return self.revoked_at is None and self.starts_at <= at < self.ends_at
