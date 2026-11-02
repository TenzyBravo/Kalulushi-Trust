"""Learners, guardians and enrollment history (design §6)."""
from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models
from django.db.models import F, Q

from core.fields import CIEmailField, period, status_check
from school.models import AcademicYear, School, SchoolClass

PHONE_PATTERN = r"^\+260[579][0-9]{8}$"


LEARNER_GENDERS = [("F", "Female"), ("M", "Male")]
LEARNER_STATUSES = [
    ("active", "Active"),
    ("transferred", "Transferred"),
    ("withdrawn", "Withdrawn"),
    ("completed", "Completed"),
]


class Learner(models.Model):
    GENDERS = LEARNER_GENDERS
    STATUSES = LEARNER_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="learners")
    learner_number = models.CharField(max_length=20, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    other_names = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDERS)
    registration_date = models.DateField()
    previous_school = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="active")
    status_changed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learners"
        ordering = ["last_name", "first_name"]
        permissions = [
            ("view_all_learners", "Can view every learner, not only those in assigned classes"),
            ("override_duplicate", "Can save a learner despite a likely-duplicate warning"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["school", "learner_number"], name="learners_unique_number"),
            status_check("learners_status_valid", LEARNER_STATUSES),
            status_check("learners_gender_valid", LEARNER_GENDERS, field="gender"),
        ]
        indexes = [models.Index(fields=["school", "last_name", "first_name"], name="learners_school_name")]
        # The trigram index on lower(first_name || ' ' || last_name) is created in migration 0002.

    def __str__(self):
        return f"{self.full_name} ({self.learner_number})"

    @property
    def full_name(self):
        return " ".join(p for p in [self.first_name, self.other_names, self.last_name] if p)

    def current_enrollment(self):
        return self.enrollments.filter(status="active").select_related("school_class").order_by("-start_date").first()

    def active_guardian_links(self):
        return (
            self.guardian_links.filter(status="active")
            .select_related("guardian")
            .order_by(models.Case(
                models.When(contact_role="primary", then=0),
                models.When(contact_role="alternative", then=1),
                default=2,
            ))
        )


class LearnerNumberSequence(models.Model):
    """Read with SELECT ... FOR UPDATE inside the registration transaction (decision D4)."""

    school = models.OneToOneField(School, on_delete=models.PROTECT, primary_key=True, related_name="+")
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "learner_number_sequences"


LEARNER_SUPPORT_NOTE_CATEGORIES = [("medical", "Medical"), ("support", "Support")]


class LearnerSupportNote(models.Model):
    """Restricted medical and support information. Reading one is audited."""

    CATEGORIES = LEARNER_SUPPORT_NOTE_CATEGORIES

    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="support_notes")
    category = models.CharField(max_length=10, choices=CATEGORIES)
    note = models.TextField(help_text="Keep to what staff need to know to care for the learner.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "learner_support_notes"
        ordering = ["-created_at"]
        constraints = [
            status_check("learner_support_notes_category_valid", LEARNER_SUPPORT_NOTE_CATEGORIES, field="category"),
            models.CheckConstraint(
                condition=Q(archived_at__isnull=True, archived_by__isnull=True)
                | Q(archived_at__isnull=False, archived_by__isnull=False),
                name="learner_support_notes_archive_complete",
            ),
        ]

    def __str__(self):
        return f"{self.get_category_display()} note for {self.learner}"


GUARDIAN_STATUSES = [("active", "Active"), ("inactive", "Inactive")]


class Guardian(models.Model):
    STATUSES = GUARDIAN_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="guardians")
    full_name = models.CharField(max_length=200)
    email = CIEmailField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "guardians"
        ordering = ["full_name"]
        constraints = [status_check("guardians_status_valid", GUARDIAN_STATUSES)]

    def __str__(self):
        return self.full_name

    def current_contact(self):
        return self.contacts.filter(is_current=True).first()


GUARDIAN_CONTACT_METHODS = [
    ("sms_code", "SMS code"),
    ("in_person", "In person"),
    ("phone_call", "Phone call"),
    ("other", "Other"),
]


class GuardianContact(models.Model):
    """Phone history: a change retires the old row and adds a new, unverified one."""

    METHODS = GUARDIAN_CONTACT_METHODS

    guardian = models.ForeignKey(Guardian, on_delete=models.PROTECT, related_name="contacts")
    phone = models.CharField(max_length=13, db_index=True)
    is_current = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    verification_method = models.CharField(max_length=12, choices=METHODS, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "guardian_contacts"
        ordering = ["-is_current", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(phone__regex=PHONE_PATTERN), name="guardian_contacts_phone_format"),
            models.UniqueConstraint(fields=["guardian"], condition=Q(is_current=True), name="guardian_contacts_one_current"),
            models.CheckConstraint(
                condition=Q(verification_method="") | Q(verification_method__in=[m for m, _ in GUARDIAN_CONTACT_METHODS]),
                name="guardian_contacts_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(verified_at__isnull=True, verified_by__isnull=True, verification_method="")
                | Q(verified_at__isnull=False, verified_by__isnull=False) & ~Q(verification_method=""),
                name="guardian_contacts_verification_complete",
            ),
            models.CheckConstraint(
                condition=Q(is_current=True, retired_at__isnull=True) | Q(is_current=False, retired_at__isnull=False),
                name="guardian_contacts_retired_iff_not_current",
            ),
        ]

    def __str__(self):
        return self.phone

    @property
    def is_verified(self):
        return self.verified_at is not None


LEARNER_GUARDIAN_ROLES = [("primary", "Primary contact"), ("alternative", "Alternative contact"), ("none", "Not a contact")]
LEARNER_GUARDIAN_STATUSES = [("active", "Active"), ("removed", "Removed")]


class LearnerGuardian(models.Model):
    ROLES = LEARNER_GUARDIAN_ROLES
    STATUSES = LEARNER_GUARDIAN_STATUSES

    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="guardian_links")
    guardian = models.ForeignKey(Guardian, on_delete=models.PROTECT, related_name="learner_links")
    relationship = models.CharField(max_length=50, help_text="e.g. Mother, Uncle")
    contact_role = models.CharField(max_length=12, choices=ROLES, default="none")
    receives_results = models.BooleanField(default=True, help_text="Authorized to receive this learner's results.")
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    removal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_guardians"
        constraints = [
            models.UniqueConstraint(
                fields=["learner", "guardian"], condition=Q(status="active"), name="learner_guardians_unique_active"
            ),
            models.UniqueConstraint(
                fields=["learner"],
                condition=Q(contact_role="primary", status="active"),
                name="learner_guardians_one_primary",
            ),
            models.UniqueConstraint(
                fields=["learner"],
                condition=Q(contact_role="alternative", status="active"),
                name="learner_guardians_one_alternative",
            ),
            status_check("learner_guardians_status_valid", LEARNER_GUARDIAN_STATUSES),
            status_check("learner_guardians_role_valid", LEARNER_GUARDIAN_ROLES, field="contact_role"),
            models.CheckConstraint(
                condition=Q(status="active", removed_at__isnull=True)
                | Q(status="removed", removed_at__isnull=False, removed_by__isnull=False),
                name="learner_guardians_removal_complete",
            ),
        ]

    def __str__(self):
        return f"{self.guardian} ({self.relationship}) for {self.learner}"


ENROLLMENT_STATUSES = [("active", "Active"), ("ended", "Ended"), ("reversed", "Reversed")]
ENROLLMENT_END_REASONS = [
    ("transfer", "Transfer"),
    ("promotion", "Promotion"),
    ("repeat", "Repeat"),
    ("withdrawal", "Withdrawal"),
    ("completion", "Completion"),
    ("correction", "Correction"),
]


class Enrollment(models.Model):
    STATUSES = ENROLLMENT_STATUSES
    END_REASONS = ENROLLMENT_END_REASONS

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="+")
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="enrollments")
    # Also covered by the composite key (class_id, academic_year_id) -> classes, added in migration 0002,
    # so a class from another year is impossible.
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.PROTECT, related_name="enrollments", db_column="class_id"
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
    end_reason = models.CharField(max_length=12, choices=END_REASONS, blank=True)
    previous_enrollment = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="next_enrollments"
    )
    request_id = models.BigIntegerField(null=True, blank=True, help_text="Promotion/transfer request (slice 6).")
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "enrollments"
        verbose_name = "enrolment"
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="enrollments_dates_ordered",
            ),
            models.CheckConstraint(
                condition=~Q(status="ended") | ~Q(end_reason="") & Q(end_date__isnull=False),
                name="enrollments_ended_has_reason_and_date",
            ),
            models.CheckConstraint(
                condition=Q(end_reason="") | Q(end_reason__in=[r for r, _ in ENROLLMENT_END_REASONS]),
                name="enrollments_end_reason_valid",
            ),
            ExclusionConstraint(
                name="enrollments_no_overlap",
                expressions=[("learner", RangeOperators.EQUAL), (period(), RangeOperators.OVERLAPS)],
                condition=~Q(status="reversed"),
                violation_error_message="This learner already has an enrolment covering these dates.",
            ),
            status_check("enrollments_status_valid", ENROLLMENT_STATUSES),
        ]
        indexes = [
            models.Index(fields=["school_class"], condition=Q(status="active"), name="enrollments_active_by_class"),
        ]

    def __str__(self):
        return f"{self.learner} in {self.school_class}"
