from django.contrib import admin
from django.db import transaction

from . import audit
from .models import AuditLog, School, SchoolSettings

admin.site.site_header = "School administration"
admin.site.site_title = "School administration"


class AuditedAdmin(admin.ModelAdmin):
    """Records every admin add/change in the audit log. Records end by status, never by deletion."""

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        before = {f: form.initial.get(f) for f in form.changed_data}
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            after = {f: form.cleaned_data.get(f) for f in form.changed_data}
            name = obj._meta.model_name
            audit.record(f"{name}.update" if change else f"{name}.create", obj, changes=audit.diff(before, after),
                         actor=request.user)


class SchoolSettingsInline(admin.StackedInline):
    model = SchoolSettings
    can_delete = False


@admin.register(School)
class SchoolAdmin(AuditedAdmin):
    list_display = ["name", "code", "status"]
    inlines = [SchoolSettingsInline]

    def has_add_permission(self, request):
        return False  # Version 1 runs one school, created by migration.

    def get_readonly_fields(self, request, obj=None):
        # The code prefixes every learner number, so it is fixed once learners exist.
        return ["code"] if obj and obj.learners.exists() else []


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "action", "actor", "object_type", "object_id"]
    list_filter = ["action"]

    def has_module_permission(self, request):
        return False  # Read through the audit screen instead; listed here only so nothing can edit it.

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
