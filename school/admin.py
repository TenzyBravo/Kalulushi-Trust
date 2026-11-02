from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from audit.admin import AuditedModelAdmin

from . import services
from .models import AcademicYear, ClassSubject, EducationStage, Grade, School, SchoolClass, SchoolSettings, Subject, Term

admin.site.site_header = "School administration"
admin.site.site_title = "School administration"


class SchoolScopedAdmin(AuditedModelAdmin):
    """Fills in the school for new rows; Version 1 serves one school."""

    exclude = ["school"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.school_id = request.user.school_id
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Let model.clean() and constraint checks see the school before saving.
        school_id = request.user.school_id

        class Form(form):
            def _post_clean(self):
                if not self.instance.school_id:
                    self.instance.school_id = school_id
                super()._post_clean()

        return Form


@admin.register(School)
class SchoolAdmin(AuditedModelAdmin):
    list_display = ["name", "code", "status"]
    readonly_fields = ["code"]

    def has_add_permission(self, request):
        return False


@admin.register(SchoolSettings)
class SchoolSettingsAdmin(AuditedModelAdmin):
    list_display = ["school", "sms_sender_name", "motto"]

    def has_add_permission(self, request):
        return False


@admin.register(EducationStage)
class EducationStageAdmin(SchoolScopedAdmin):
    list_display = ["name", "sequence"]


@admin.register(Grade)
class GradeAdmin(SchoolScopedAdmin):
    list_display = ["name", "code", "stage", "sequence", "status"]
    list_filter = ["stage", "status"]


class TermInline(admin.TabularInline):
    model = Term
    extra = 0
    max_num = 3
    can_delete = False


@admin.register(AcademicYear)
class AcademicYearAdmin(SchoolScopedAdmin):
    list_display = ["name", "start_date", "end_date", "status"]
    readonly_fields = ["status"]
    inlines = [TermInline]
    actions = ["make_current"]

    @admin.action(description="Make the selected year current (closes the current one)", permissions=["change"])
    def make_current(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Choose exactly one year.", messages.ERROR)
            return
        try:
            year = services.make_year_current(queryset.get(), actor=request.user)
        except ValidationError as exc:
            self.message_user(request, " ".join(exc.messages), messages.ERROR)
            return
        self.message_user(request, f"{year} is now the current academic year.")


class ClassSubjectInline(admin.TabularInline):
    model = ClassSubject
    extra = 0
    can_delete = False
    autocomplete_fields = ["subject"]


@admin.register(SchoolClass)
class SchoolClassAdmin(SchoolScopedAdmin):
    list_display = ["name", "academic_year", "grade", "status"]
    list_filter = ["academic_year", "grade__stage", "status"]
    search_fields = ["name"]
    inlines = [ClassSubjectInline]


@admin.register(Subject)
class SubjectAdmin(SchoolScopedAdmin):
    list_display = ["name", "code", "stage", "status"]
    list_filter = ["stage", "status"]
    search_fields = ["name", "code"]


@admin.register(ClassSubject)
class ClassSubjectAdmin(AuditedModelAdmin):
    list_display = ["school_class", "subject", "status"]
    list_filter = ["school_class__academic_year", "status"]
    search_fields = ["school_class__name", "subject__name"]
    autocomplete_fields = ["school_class", "subject"]
