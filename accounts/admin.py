"""Teacher assignments and cover in the admin. Staff accounts are managed on the Staff pages,
where the role limits (decision D6) apply."""
from django.contrib import admin, messages

from audit.admin import AuditedModelAdmin

from . import services
from .models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment


class StampedAdmin(AuditedModelAdmin):
    stamp_field = "assigned_by"

    def get_exclude(self, request, obj=None):
        return [self.stamp_field, *getattr(self, "extra_exclude", [])]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        stamp_field, user, prepare = self.stamp_field, request.user, self.prepare

        class Form(form):
            def _post_clean(self):
                if not getattr(self.instance, f"{stamp_field}_id"):
                    setattr(self.instance, stamp_field, user)
                prepare(self.instance)
                super()._post_clean()

        return Form

    def prepare(self, obj):
        pass


@admin.register(TeacherAssignment)
class TeacherAssignmentAdmin(StampedAdmin):
    list_display = ["class_subject", "teacher", "assignment_type", "start_date", "end_date", "status"]
    list_filter = ["assignment_type", "status", "class_subject__school_class__academic_year"]
    search_fields = ["teacher__first_name", "teacher__last_name", "class_subject__school_class__name"]
    autocomplete_fields = ["class_subject"]
    extra_exclude = ["school"]

    def prepare(self, obj):
        if obj.class_subject_id and not obj.school_id:
            obj.school_id = obj.class_subject.school_class.school_id

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "teacher":
            kwargs["queryset"] = db_field.related_model.objects.filter(is_active=True, groups__name="Teacher")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ClassTeacherAssignment)
class ClassTeacherAssignmentAdmin(StampedAdmin):
    list_display = ["school_class", "teacher", "start_date", "end_date"]
    list_filter = ["school_class__academic_year"]
    autocomplete_fields = ["school_class"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "teacher":
            kwargs["queryset"] = db_field.related_model.objects.filter(is_active=True, groups__name="Teacher")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(CoverAuthorization)
class CoverAuthorizationAdmin(StampedAdmin):
    stamp_field = "authorized_by"
    extra_exclude = ["revoked_at", "revoked_by"]
    list_display = ["teacher_assignment", "cover_teacher", "starts_at", "ends_at", "in_force", "revoked_at"]
    list_filter = ["revoked_at"]
    actions = ["revoke"]

    @admin.display(boolean=True, description="In force")
    def in_force(self, obj):
        return obj.is_in_force

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "teacher_assignment":
            kwargs["queryset"] = TeacherAssignment.objects.filter(assignment_type="primary", status="active")
        if db_field.name == "cover_teacher":
            kwargs["queryset"] = db_field.related_model.objects.filter(is_active=True, groups__name="Teacher")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.action(description="Revoke the selected cover now", permissions=["change"])
    def revoke(self, request, queryset):
        for cover in queryset.filter(revoked_at__isnull=True):
            services.revoke_cover(request.user, cover)
        self.message_user(request, "Cover revoked.", messages.SUCCESS)
