"""Append-only audit log (design §7)."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    school = models.ForeignKey("school.School", on_delete=models.PROTECT, null=True, related_name="+")
    occurred_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=50, blank=True)
    object_id = models.BigIntegerField(null=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True)
    request_id = models.UUIDField(null=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["school", "-occurred_at"], name="audit_logs_school_time"),
            models.Index(fields=["object_type", "object_id"], name="audit_logs_object"),
            models.Index(fields=["actor", "-occurred_at"], name="audit_logs_actor_time"),
        ]

    def __str__(self):
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action} by {self.actor or 'system'}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Audit records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Audit records cannot be deleted.")
