from django.conf import settings
from django.db import models
from django.db.models import Q

from .db import choices, status_check


class School(models.Model):
    name = models.TextField()
    code = models.TextField(unique=True, help_text="Short code, e.g. KLT. Prefixes learner numbers.")
    address = models.TextField(blank=True)
    phone = models.TextField(blank=True)
    email = models.TextField(blank=True)
    status = models.TextField(choices=choices("active", "inactive"), default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "schools"
        constraints = [status_check("schools_status_valid", "status", ["active", "inactive"])]

    def __str__(self):
        return self.name

    @classmethod
    def current(cls):
        """Version 1 runs one school; this is the single place that assumption lives."""
        return cls.objects.order_by("id").first()


class SchoolSettings(models.Model):
    school = models.OneToOneField(School, on_delete=models.PROTECT, primary_key=True, related_name="settings")
    sms_sender_name = models.TextField(blank=True, help_text="Up to 11 characters, shown as the SMS sender.")
    motto = models.TextField(blank=True)
    logo = models.FileField(upload_to="logos/", blank=True)

    class Meta:
        db_table = "school_settings"
        verbose_name = verbose_name_plural = "school settings"
        constraints = [
            models.CheckConstraint(
                condition=Q(sms_sender_name__regex=r"^.{0,11}$"), name="school_settings_sender_max_11"
            )
        ]

    def __str__(self):
        return f"Settings for {self.school}"


class AuditLog(models.Model):
    """Append-only. A database trigger rejects UPDATE and DELETE."""

    school = models.ForeignKey(School, on_delete=models.PROTECT, null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    action = models.TextField()
    object_type = models.TextField(blank=True)
    object_id = models.BigIntegerField(null=True, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["school", "-occurred_at"], name="audit_school_time"),
            models.Index(fields=["object_type", "object_id"], name="audit_object"),
            models.Index(fields=["actor", "-occurred_at"], name="audit_actor_time"),
        ]
        permissions = [("view_audit_log", "Can view the audit log")]

    def __str__(self):
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Audit records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit records cannot be deleted.")
