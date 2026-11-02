from django.contrib import admin

from . import log
from .models import AuditLog


class AuditedModelAdmin(admin.ModelAdmin):
    """Admin pages that audit every save and never delete (design §2, §7).

    The admin wraps each save in a transaction, so the audit record commits with the change.
    """

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change:
            changes = {f: [form.initial.get(f), form.cleaned_data.get(f)] for f in form.changed_data}
            if not changes:
                return
        else:
            changes = {f: form.cleaned_data.get(f) for f in form.changed_data}
        log.record(f"{obj._meta.model_name}.{'update' if change else 'create'}", obj, changes=changes, actor=request.user)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "action", "actor", "object_type", "object_id", "reason"]
    list_filter = ["action", "object_type"]
    search_fields = ["action", "reason"]
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
