from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group, PermissionsMixin
from django.db import models
from django.db.models import Q

from core.db import CITextField
from core.models import School
from core.phone import normalise_phone

from . import roles


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _next_staff_number(self, school, prefix="SA"):
        n = 1
        while self.filter(school=school, staff_number=f"{prefix}-{n:04d}").exists():
            n += 1
        return f"{prefix}-{n:04d}"

    def create_user(self, username, password=None, **fields):
        fields.setdefault("school", School.current())
        if not fields.get("staff_number"):
            fields["staff_number"] = self._next_staff_number(fields["school"], "ST")
        email = fields.pop("email", None)
        user = self.model(username=username, email=self.normalize_email(email) or None, **fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **fields):
        fields["is_superuser"] = True
        fields.setdefault("school", School.current())
        if not fields.get("staff_number"):
            fields["staff_number"] = self._next_staff_number(fields["school"])
        user = self.create_user(username, password, email=email, **fields)
        user.groups.add(Group.objects.get_or_create(name=roles.SYSTEM_ADMIN)[0])
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """Every staff member who signs in. There is no separate staff table (decision D2)."""

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="users")
    staff_number = models.TextField()
    username = CITextField(unique=True)
    email = CITextField(null=True, blank=True)
    title = models.TextField(blank=True, help_text="e.g. Mr, Mrs, Ms, Dr")
    first_name = models.TextField(blank=True)
    last_name = models.TextField(blank=True)
    phone = models.TextField(blank=True, help_text="Stored as +260XXXXXXXXX")
    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "users"
        ordering = ["last_name", "first_name", "username"]
        constraints = [
            models.UniqueConstraint(fields=["school", "staff_number"], name="users_staff_number_unique"),
            models.UniqueConstraint(fields=["email"], condition=Q(email__isnull=False), name="users_email_unique"),
            models.CheckConstraint(
                condition=Q(deactivated_at__isnull=True, deactivated_by__isnull=True)
                | Q(deactivated_at__isnull=False, deactivated_by__isnull=False),
                name="users_deactivation_both_or_neither",
            ),
        ]
        permissions = [
            ("manage_staff", "Can create staff accounts and assign roles"),
            ("deactivate_staff", "Can deactivate staff accounts"),
        ]

    def __str__(self):
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip() or None
        if self.phone:
            self.phone = normalise_phone(self.phone)
        super().save(*args, **kwargs)

    def get_full_name(self):
        return " ".join(p for p in [self.title, self.first_name, self.last_name] if p).strip()

    def get_short_name(self):
        return self.first_name or self.username

    @property
    def role_names(self):
        return [g.name for g in self.groups.all() if g.name in roles.ALL_ROLES]

    def has_role(self, *names):
        return self.is_superuser and roles.SYSTEM_ADMIN in names or self.groups.filter(name__in=names).exists()

    @property
    def is_staff(self):
        """Who may open the admin site (school structure screens)."""
        return self.is_active and (self.is_superuser or self.has_role(*roles.ADMIN_SITE_ROLES))


class UserSession(models.Model):
    """One row per signed-in session, so administrators can see and revoke them."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.TextField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        db_table = "user_sessions"
        ordering = ["-last_seen_at"]
