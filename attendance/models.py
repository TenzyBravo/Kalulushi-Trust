"""Calendar, daily attendance and absence follow-up (slice 2 design, docs/database-design-attendance.md)."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.db import choices, status_check
from core.models import School
from learners.models import Enrollment, Guardian, Learner
from school.models import AcademicYear, ClassGroup, Grade

# no_alert_day: registers are taken but absences never start or extend a case; event: shown only.
EVENT_KINDS = ["holiday", "closure", "extra_school_day", "no_alert_day", "event"]
EVENT_SOURCES = ["manual", "upload", "seed"]
STATUSES = ["present", "absent", "late", "excused"]
CASE_STATUSES = ["new", "follow_up_pending", "in_progress", "resolved", "escalated"]
ACTION_KINDS = ["contact", "note", "status_change", "referral", "system_note"]
CONTACT_METHODS = ["phone_call", "whatsapp", "sms_sent_manually", "home_visit", "met_at_school", "other"]
REASON_CATEGORIES = ["illness", "family", "transport", "fees", "work", "unknown", "other"]


class CalendarEvent(models.Model):
    """Exceptions to term time. A closure can apply to the whole school, one grade or one class (§3, §3.1)."""

    HOLIDAY, CLOSURE, EXTRA_SCHOOL_DAY, NO_ALERT_DAY, EVENT = EVENT_KINDS

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="calendar_events")
    kind = models.TextField(choices=choices(*EVENT_KINDS))
    name = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Blank for a grade or class closure means until the end of the term.")
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="+", db_column="class_id")
    reason = models.TextField(blank=True)
    source = models.TextField(choices=choices(*EVENT_SOURCES), default="manual",
                              help_text="Uploaded items are replaced when the year's calendar is uploaded again.")
    superseded_at = models.DateTimeField(null=True, blank=True, help_text="Set when a later upload replaced it; kept for history.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "calendar_events"
        ordering = ["start_date", "id"]
        permissions = [("add_closure", "Can close the school, a grade or a class")]
        constraints = [
            status_check("calendar_events_kind_valid", "kind", EVENT_KINDS),
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="calendar_events_dates_ordered"
            ),
            models.CheckConstraint(
                condition=Q(grade__isnull=True) | Q(class_group__isnull=True), name="calendar_events_one_scope"
            ),
            models.CheckConstraint(
                condition=Q(kind="closure") | Q(grade__isnull=True, class_group__isnull=True),
                name="calendar_events_only_closures_scoped",
            ),
            models.CheckConstraint(
                condition=Q(end_date__isnull=False) | Q(grade__isnull=False) | Q(class_group__isnull=False),
                name="calendar_events_open_end_only_scoped",
            ),
            models.CheckConstraint(
                condition=~Q(kind="closure") | ~Q(reason=""), name="calendar_events_closure_reason"
            ),
            status_check("calendar_events_source_valid", "source", EVENT_SOURCES),
        ]
        indexes = [models.Index(fields=["academic_year", "start_date"], condition=Q(superseded_at__isnull=True),
                                name="calendar_events_in_force")]

    def __str__(self):
        return f"{self.name} ({self.start_date}{'' if self.end_date == self.start_date else f' to {self.end_date or 'end of term'}'})"

    @property
    def scope_label(self):
        if self.class_group_id:
            return f"Class {self.class_group.name}"
        if self.grade_id:
            return self.grade.name
        return "Whole school"


class AttendanceSession(models.Model):
    """One register per class per date (§4)."""

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="attendance_sessions", db_column="class_id")
    # Also covered by the composite FK (class_id, academic_year_id) -> classes, added in migration 0002.
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    last_saved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_sessions"
        ordering = ["-date", "class_group"]
        permissions = [
            ("take_register", "Can take the daily register for a class they are class teacher of"),
            ("correct_attendance", "Can correct any register, with a reason"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["class_group", "date"], name="attendance_sessions_one_per_class_day"),
            # Target of the composite foreign key from attendance_records.
            models.UniqueConstraint(fields=["id", "class_group"], name="attendance_sessions_id_class_unique"),
        ]

    def __str__(self):
        return f"{self.class_group.name} register, {self.date}"


class AttendanceRecord(models.Model):
    PRESENT, ABSENT, LATE, EXCUSED = STATUSES

    session = models.ForeignKey(AttendanceSession, on_delete=models.PROTECT, related_name="records")
    # class_id, enrollment_id and learner_id are tied together by the composite keys added in migration 0002.
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="+", db_column="class_id")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="attendance_records")
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="attendance_records")
    status = models.TextField(choices=choices(*STATUSES))
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_records"
        ordering = ["session", "learner__last_name", "learner__first_name"]
        constraints = [
            # No duplicate record for a learner in a register (baseline §6).
            models.UniqueConstraint(fields=["session", "learner"], name="attendance_records_one_per_learner"),
            status_check("attendance_records_status_valid", "status", STATUSES),
        ]
        indexes = [models.Index(fields=["learner", "session"], name="attendance_records_learner")]

    def __str__(self):
        return f"{self.learner} {self.status} on {self.session.date}"


class AbsenceCase(models.Model):
    NEW, FOLLOW_UP_PENDING, IN_PROGRESS, RESOLVED, ESCALATED = CASE_STATUSES
    OPEN = [NEW, FOLLOW_UP_PENDING, IN_PROGRESS, ESCALATED]

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="absence_cases")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="absence_cases", db_column="class_id")
    assigned_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="absence_cases"
    )
    status = models.TextField(choices=choices(*CASE_STATUSES), default=NEW)
    first_absent_date = models.DateField()
    last_absent_date = models.DateField()
    days_absent = models.SmallIntegerField(default=2)
    flagged_at = models.DateTimeField(auto_now_add=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    escalation_reason = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "absence_cases"
        ordering = ["-last_absent_date", "-id"]
        permissions = [("review_escalations", "Can review and close escalated absence cases")]
        constraints = [
            status_check("absence_cases_status_valid", "status", CASE_STATUSES),
            models.CheckConstraint(
                condition=Q(last_absent_date__gte=F("first_absent_date")), name="absence_cases_dates_ordered"
            ),
            models.CheckConstraint(
                condition=Q(status="resolved", resolved_at__isnull=False, resolved_by__isnull=False) & ~Q(resolution="")
                | ~Q(status="resolved") & Q(resolved_at__isnull=True, resolved_by__isnull=True, resolution=""),
                name="absence_cases_resolution_complete",
            ),
            # One open case per learner; later absences extend it (§5.2).
            models.UniqueConstraint(fields=["learner"], condition=~Q(status="resolved"), name="absence_cases_one_open"),
        ]

    def __str__(self):
        return f"Absence case for {self.learner} from {self.first_absent_date}"

    @property
    def is_open(self):
        return self.status != self.RESOLVED


class AbsenceCaseRecord(models.Model):
    """The absences that triggered or extended a case."""

    case = models.ForeignKey(AbsenceCase, on_delete=models.PROTECT, related_name="case_records")
    attendance_record = models.ForeignKey(AttendanceRecord, on_delete=models.PROTECT, related_name="case_links")

    class Meta:
        db_table = "absence_case_records"
        constraints = [models.UniqueConstraint(fields=["case", "attendance_record"], name="absence_case_records_unique")]


class FollowUpAction(models.Model):
    """The case history. Append-only: a trigger refuses UPDATE and DELETE (§5.5)."""

    case = models.ForeignKey(AbsenceCase, on_delete=models.PROTECT, related_name="actions")
    kind = models.TextField(choices=choices(*ACTION_KINDS))
    contact_date = models.DateField(null=True, blank=True)
    contact_method = models.TextField(choices=choices(*CONTACT_METHODS), blank=True)
    contacted_guardian = models.ForeignKey(Guardian, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    outcome = models.TextField(blank=True)
    reason_category = models.TextField(choices=choices(*REASON_CATEGORIES), blank=True)
    reason_text = models.TextField(blank=True)
    next_action = models.TextField(blank=True)
    next_action_due = models.DateField(null=True, blank=True)
    status_from = models.TextField(blank=True)
    status_to = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "follow_up_actions"
        ordering = ["created_at", "id"]
        constraints = [
            status_check("follow_up_actions_kind_valid", "kind", ACTION_KINDS),
            models.CheckConstraint(
                condition=~Q(kind="contact") | Q(contact_date__isnull=False) & ~Q(contact_method=""),
                name="follow_up_actions_contact_complete",
            ),
            models.CheckConstraint(
                condition=Q(contact_method="") | Q(contact_method__in=CONTACT_METHODS), name="follow_up_actions_method_valid"
            ),
            models.CheckConstraint(
                condition=Q(reason_category="") | Q(reason_category__in=REASON_CATEGORIES), name="follow_up_actions_reason_valid"
            ),
            models.CheckConstraint(
                condition=Q(kind="system_note") | Q(created_by__isnull=False), name="follow_up_actions_author_required"
            ),
        ]

    def __str__(self):
        return f"{self.kind} on case {self.case_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Follow-up actions cannot be changed.")
        super().save(*args, **kwargs)
