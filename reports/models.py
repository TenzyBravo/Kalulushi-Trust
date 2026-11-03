from django.conf import settings
from django.db import models

from core.db import choices, status_check
from core.models import School

FORMATS = ["xlsx", "print", "pdf"]


class ReportExport(models.Model):
    """One row per export (reports design §3). Append-only: a trigger refuses UPDATE and DELETE."""

    school = models.ForeignKey(School, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    report = models.TextField()
    format = models.TextField(choices=choices(*FORMATS))
    filters = models.JSONField(default=dict, blank=True)
    row_count = models.IntegerField(default=0)
    sensitive = models.BooleanField(default=False, help_text="Contains learner-level personal data (decision R2).")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "report_exports"
        ordering = ["-created_at", "-id"]
        permissions = [("view_reports", "Can open reports for the classes they can see")]
        constraints = [status_check("report_exports_format_valid", "format", FORMATS)]
        indexes = [models.Index(fields=["school", "-created_at"], name="report_exports_school_time")]

    def __str__(self):
        return f"{self.report} ({self.format}) by {self.user}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Export records cannot be changed.")
        super().save(*args, **kwargs)
