from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Concat, Lower

from core.db import CITextField, DateRange, choices, status_check
from core.models import School
from school.models import AcademicYear, ClassGroup

PHONE_PATTERN = r"^\+260[579][0-9]{8}$"
LEARNER_STATUSES = ["active", "transferred", "withdrawn", "completed"]
END_REASONS = ["transfer", "promotion", "repeat", "withdrawal", "completion", "correction"]
CONTACT_METHODS = ["sms_code", "in_person", "phone_call", "other"]


class Learner(models.Model):
    ACTIVE, TRANSFERRED, WITHDRAWN, COMPLETED = "active", "transferred", "withdrawn", "completed"
    STATUSES = LEARNER_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="learners")
    learner_number = models.TextField(editable=False)
    first_name = models.TextField()
    last_name = models.TextField()
    other_names = models.TextField(blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.TextField(choices=[("F", "Female"), ("M", "Male")], blank=True)
    registration_date = models.DateField()
    previous_school = models.TextField(blank=True)
    status = models.TextField(choices=choices(*STATUSES), default=ACTIVE)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    guardians = models.ManyToManyField("Guardian", through="LearnerGuardian", related_name="learners")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learners"
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "learner_number"], name="learners_number_unique"),
            status_check("learners_status_valid", "status", LEARNER_STATUSES),
            models.CheckConstraint(condition=Q(gender__in=["F", "M", ""]), name="learners_gender_valid"),
        ]
        indexes = [
            models.Index(fields=["school", "last_name", "first_name"], name="learners_name"),
            GinIndex(
                OpClass(Lower(Concat("first_name", Value(" "), "last_name", output_field=models.TextField())), name="gin_trgm_ops"),
                name="learners_name_trgm",
            ),
        ]
        permissions = [
            ("override_duplicate", "Can save a learner despite a likely-duplicate warning"),
            ("view_support_notes", "Can view learner medical and support notes"),
            ("transfer_learner", "Can transfer a learner between classes"),
            ("import_learners", "Can bulk-import learners"),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.learner_number})"

    @property
    def full_name(self):
        return " ".join(p for p in [self.first_name, self.other_names, self.last_name] if p)

    def current_enrollment(self):
        return self.enrollments.filter(status="active", end_date__isnull=True).select_related("class_group").first()

    def active_links(self):
        return self.guardian_links.filter(status="active").select_related("guardian")


class LearnerNumberSequence(models.Model):
    """Read with SELECT … FOR UPDATE inside the registration transaction (D4)."""

    school = models.OneToOneField(School, on_delete=models.PROTECT, primary_key=True)
    last_value = models.IntegerField(default=0)

    class Meta:
        db_table = "learner_number_sequences"


class LearnerSupportNote(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="support_notes")
    category = models.TextField(choices=choices("medical", "support"))
    note = models.TextField(help_text="Keep to what is operationally necessary.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        db_table = "learner_support_notes"
        ordering = ["-created_at"]
        constraints = [status_check("support_notes_category_valid", "category", ["medical", "support"])]


class Guardian(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="guardians")
    full_name = models.TextField()
    email = CITextField(blank=True)
    status = models.TextField(choices=choices("active", "inactive"), default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "guardians"
        ordering = ["full_name"]
        constraints = [status_check("guardians_status_valid", "status", ["active", "inactive"])]

    def __str__(self):
        return self.full_name

    def current_contact(self):
        return self.contacts.filter(is_current=True).first()

    @property
    def current_phone(self):
        contact = self.current_contact()
        return contact.phone if contact else ""


class GuardianContact(models.Model):
    """Phone history: a changed number retires the old row instead of overwriting it.

    Numbers are not unique across guardians (D3): families share handsets.
    """

    METHODS = CONTACT_METHODS

    guardian = models.ForeignKey(Guardian, on_delete=models.PROTECT, related_name="contacts")
    phone = models.TextField()
    is_current = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    verification_method = models.TextField(choices=choices(*METHODS), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "guardian_contacts"
        ordering = ["-is_current", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(phone__regex=PHONE_PATTERN), name="contacts_phone_format"),
            models.UniqueConstraint(fields=["guardian"], condition=Q(is_current=True), name="contacts_one_current"),
            models.CheckConstraint(
                condition=Q(verification_method__in=["", *CONTACT_METHODS]), name="contacts_method_valid"
            ),
        ]
        indexes = [models.Index(fields=["phone"], name="contacts_phone")]

    def __str__(self):
        return self.phone

    @property
    def is_verified(self):
        return self.verified_at is not None


class LearnerGuardian(models.Model):
    PRIMARY, ALTERNATIVE, NONE = "primary", "alternative", "none"

    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="guardian_links")
    guardian = models.ForeignKey(Guardian, on_delete=models.PROTECT, related_name="learner_links")
    relationship = models.TextField(blank=True, help_text="e.g. Mother, Uncle")
    contact_role = models.TextField(choices=choices(PRIMARY, ALTERNATIVE, NONE), default=NONE)
    receives_results = models.BooleanField(default=True)
    status = models.TextField(choices=choices("active", "removed"), default="active")
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    removal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_guardians"
        ordering = ["contact_role", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["learner", "guardian"], condition=Q(status="active"), name="links_unique_active"),
            models.UniqueConstraint(
                fields=["learner"], condition=Q(contact_role="primary", status="active"), name="links_one_primary"
            ),
            models.UniqueConstraint(
                fields=["learner"], condition=Q(contact_role="alternative", status="active"), name="links_one_alternative"
            ),
            status_check("links_status_valid", "status", ["active", "removed"]),
            status_check("links_role_valid", "contact_role", ["primary", "alternative", "none"]),
        ]


class Enrollment(models.Model):
    END_REASONS = END_REASONS

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    learner = models.ForeignKey(Learner, on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="enrollments")
    # Also covered by the composite FK (class_id, academic_year_id) added in migration 0002.
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="enrollments", db_column="class_id")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.TextField(choices=choices("active", "ended", "reversed"), default="active")
    end_reason = models.TextField(choices=choices(*END_REASONS), blank=True)
    previous_enrollment = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    request_id = models.BigIntegerField(null=True, blank=True, help_text="Promotion/transfer request (slice 6).")
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "enrollments"
        ordering = ["-start_date", "-id"]
        verbose_name = "enrolment"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="enrollments_dates_ordered"
            ),
            status_check("enrollments_status_valid", "status", ["active", "ended", "reversed"]),
            models.CheckConstraint(
                condition=~Q(status="ended") | Q(end_reason__in=END_REASONS), name="enrollments_end_reason_required"
            ),
            models.CheckConstraint(
                condition=Q(end_reason="") | Q(end_reason__in=END_REASONS), name="enrollments_end_reason_valid"
            ),
            # Two overlapping enrollments for one learner cannot both commit, even concurrently (§5).
            ExclusionConstraint(
                name="enrollments_no_overlap",
                expressions=[
                    ("learner", RangeOperators.EQUAL),
                    (DateRange(F("start_date"), F("end_date"), Value("[]")), RangeOperators.OVERLAPS),
                ],
                condition=~Q(status="reversed"),
            ),
        ]
        indexes = [models.Index(fields=["class_group"], condition=Q(status="active"), name="enrollments_active_class")]

    def __str__(self):
        return f"{self.learner.full_name} in {self.class_group.name} from {self.start_date}"
