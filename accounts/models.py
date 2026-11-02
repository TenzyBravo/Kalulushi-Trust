"""Staff, sessions, teaching assignments and cover (design §5)."""
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.fields import CICharField, CIEmailField, overlapping, period, status_check
from core.phone import normalise_phone, validate_phone
from school.models import ClassSubject, School, SchoolClass

from . import roles


class UserManager(DjangoUserManager):
    def _create_user(self, username, email, password, **extra_fields):
        if "school" not in extra_fields and "school_id" not in extra_fields:
            extra_fields["school"] = School.current()
        return super()._create_user(username, email or None, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        user = super().create_superuser(username, email, password, **extra_fields)
        user.set_roles([roles.SYSTEM_ADMIN])
        return user


class User(AbstractUser):
    """A staff member who signs in. There is no separate staff table (decision D2)."""

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="users")
    staff_number = models.CharField(max_length=30)
    username = CICharField(
        max_length=150,
        unique=True,
        validators=[UnicodeUsernameValidator()],
        error_messages={"unique": "A user with that username already exists."},
    )
    email = CIEmailField(null=True, blank=True)
    title = models.CharField(max_length=20, blank=True, help_text="e.g. Mr, Mrs, Ms, Dr")
    phone = models.CharField(max_length=13, blank=True, validators=[validate_phone])
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    date_joined = None

    REQUIRED_FIELDS = ["staff_number", "first_name", "last_name"]

    objects = UserManager()

    class Meta:
        db_table = "users"
        ordering = ["last_name", "first_name"]
        permissions = [("deactivate_user", "Can deactivate staff accounts")]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "staff_number"],
                name="users_unique_staff_number",
                violation_error_message="Another account already has this staff number.",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=Q(email__isnull=False),
                name="users_unique_email",
                violation_error_message="Another account already uses this email address.",
            ),
            models.CheckConstraint(
                condition=Q(deactivated_at__isnull=True, deactivated_by__isnull=True)
                | Q(deactivated_at__isnull=False, deactivated_by__isnull=False),
                name="users_deactivation_complete",
            ),
        ]

    def __str__(self):
        return self.display_name

    def clean(self):
        super().clean()
        self.email = self.email or None
        if self.phone:
            self.phone = normalise_phone(self.phone)

    @property
    def display_name(self):
        return " ".join(p for p in [self.title, self.first_name, self.last_name] if p) or self.username

    def role_names(self):
        if not hasattr(self, "_role_names"):
            self._role_names = set(self.groups.filter(name__in=roles.ALL_ROLES).values_list("name", flat=True))
        return self._role_names

    def has_role(self, *names):
        return bool(self.role_names() & set(names))

    def set_roles(self, names):
        from django.contrib.auth.models import Group

        unknown = set(names) - set(roles.ALL_ROLES)
        if unknown:
            raise ValueError(f"Unknown roles: {sorted(unknown)}")
        current = self.groups.filter(name__in=roles.ALL_ROLES)
        self.groups.remove(*current.exclude(name__in=names))
        self.groups.add(*[Group.objects.get_or_create(name=n)[0] for n in names])
        self._role_names = set(names)
        is_staff = self.is_superuser or bool(set(names) & roles.ADMIN_SITE_ROLES)
        if self.is_staff != is_staff:
            self.is_staff = is_staff
            self.save(update_fields=["is_staff"])


class UserSession(models.Model):
    """Lets administrators see and revoke a user's active sessions."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        db_table = "user_sessions"
        ordering = ["-last_seen_at"]

    def __str__(self):
        return f"{self.user} from {self.ip_address or 'unknown'}"


TEACHER_ASSIGNMENT_TYPES = [("primary", "Primary"), ("backup", "Backup")]
TEACHER_ASSIGNMENT_STATUSES = [("active", "Active"), ("ended", "Ended")]


class TeacherAssignment(models.Model):
    TYPES = TEACHER_ASSIGNMENT_TYPES
    STATUSES = TEACHER_ASSIGNMENT_STATUSES

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="+")
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name="teacher_assignments")
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name="teacher_assignments")
    assignment_type = models.CharField(
        max_length=10, choices=TYPES, default="primary", help_text="A backup grants nothing until cover is authorized."
    )
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank while ongoing.")
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "teacher_assignments"
        ordering = ["class_subject", "-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="teacher_assignments_dates_ordered",
                violation_error_message="The end date cannot be before the start date.",
            ),
            ExclusionConstraint(
                name="teacher_assignments_one_primary",
                expressions=[("class_subject", RangeOperators.EQUAL), (period(), RangeOperators.OVERLAPS)],
                condition=Q(assignment_type="primary", status="active"),
                violation_error_message="This class subject already has a primary teacher for these dates.",
            ),
            ExclusionConstraint(
                name="teacher_assignments_no_duplicate",
                expressions=[
                    ("class_subject", RangeOperators.EQUAL),
                    ("teacher", RangeOperators.EQUAL),
                    (period(), RangeOperators.OVERLAPS),
                ],
                condition=Q(status="active"),
                violation_error_message="This teacher is already assigned to this class subject for these dates.",
            ),
            status_check("teacher_assignments_status_valid", TEACHER_ASSIGNMENT_STATUSES),
            status_check("teacher_assignments_type_valid", TEACHER_ASSIGNMENT_TYPES, field="assignment_type"),
        ]

    def __str__(self):
        return f"{self.teacher} – {self.class_subject} ({self.assignment_type})"

    @classmethod
    def in_force(cls, on=None):
        on = on or timezone.localdate()
        return cls.objects.filter(overlapping(on, on), status="active")


class ClassTeacherAssignment(models.Model):
    """The class teacher, who receives absence alerts (baseline §6)."""

    school_class = models.ForeignKey(SchoolClass, on_delete=models.PROTECT, related_name="class_teacher_assignments")
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name="class_teacher_assignments")
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank while ongoing.")
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "class_teacher_assignments"
        ordering = ["school_class", "-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="class_teacher_assignments_dates_ordered",
                violation_error_message="The end date cannot be before the start date.",
            ),
            ExclusionConstraint(
                name="class_teacher_assignments_one_at_a_time",
                expressions=[("school_class", RangeOperators.EQUAL), (period(), RangeOperators.OVERLAPS)],
                violation_error_message="This class already has a class teacher for these dates.",
            ),
        ]

    def __str__(self):
        return f"{self.teacher} – class teacher of {self.school_class}"

    @classmethod
    def in_force(cls, on=None):
        on = on or timezone.localdate()
        return cls.objects.filter(overlapping(on, on))


COVER_AUTHORIZATION_DUTIES = [
    ("attendance", "Attendance"),
    ("assessment_entry", "Assessment entry"),
    ("competency_entry", "Competency entry"),
]


class CoverAuthorization(models.Model):
    DUTIES = COVER_AUTHORIZATION_DUTIES

    teacher_assignment = models.ForeignKey(
        TeacherAssignment,
        on_delete=models.PROTECT,
        related_name="cover_authorizations",
        help_text="The primary assignment being covered.",
    )
    cover_teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name="cover_authorizations")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    duties = ArrayField(models.CharField(max_length=20, choices=DUTIES))
    reason = models.TextField()
    authorized_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cover_authorizations"
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="cover_authorizations_window_ordered",
                violation_error_message="The cover must end after it starts.",
            ),
            models.CheckConstraint(
                condition=Q(duties__contained_by=[d for d, _ in COVER_AUTHORIZATION_DUTIES]) & Q(duties__len__gt=0),
                name="cover_authorizations_duties_valid",
                violation_error_message="Choose at least one duty.",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True, revoked_by__isnull=True)
                | Q(revoked_at__isnull=False, revoked_by__isnull=False),
                name="cover_authorizations_revocation_complete",
            ),
        ]

    def __str__(self):
        return f"{self.cover_teacher} covering {self.teacher_assignment.class_subject}"

    def clean(self):
        if self.teacher_assignment_id:
            assignment = self.teacher_assignment
            if assignment.assignment_type != "primary":
                raise ValidationError({"teacher_assignment": "Cover is authorized against a primary assignment."})
            if self.cover_teacher_id and self.cover_teacher_id == assignment.teacher_id:
                raise ValidationError({"cover_teacher": "The cover teacher must differ from the primary teacher."})

    @classmethod
    def in_force(cls, at=None):
        at = at or timezone.now()
        return cls.objects.filter(starts_at__lte=at, ends_at__gt=at, revoked_at__isnull=True)

    @property
    def is_in_force(self):
        now = timezone.now()
        return self.revoked_at is None and self.starts_at <= now < self.ends_at
