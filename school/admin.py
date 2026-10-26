from django.contrib import admin, messages
from django.db import transaction

from core import audit
from core.admin import AuditedAdmin
from core.models import School

from .models import AcademicYear, ClassGroup, ClassSubject, EducationStage, Grade, Subject, Term
from .services import make_year_current


class SchoolScopedAdmin(AuditedAdmin):
    """Fills in the (single) school so staff never pick it."""

    exclude = ["school"]

    def save_model(self, request, obj, form, change):
        if not obj.school_id:
            obj.school = School.current()
        super().save_model(request, obj, form, change)


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
    can_delete = False


@admin.register(AcademicYear)
class AcademicYearAdmin(SchoolScopedAdmin):
    list_display = ["name", "start_date", "end_date", "status"]
    readonly_fields = ["status"]
    inlines = [TermInline]
    actions = ["make_current"]

    def save_formset(self, request, form, formset, change):
        with transaction.atomic():
            for obj in formset.save(commit=False):
                created = obj.pk is None
                before = {} if created else audit.snapshot(Term.objects.get(pk=obj.pk), ["start_date", "end_date", "number"])
                obj.save()
                after = audit.snapshot(obj, ["start_date", "end_date", "number"])
                audit.record("term.create" if created else "term.update", obj, changes=audit.diff(before, after),
                             school=obj.academic_year.school, actor=request.user)

    @admin.action(description="Make the selected year current (closes the current one)", permissions=["change"])
    def make_current(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one year.", messages.ERROR)
            return
        year = make_year_current(queryset.get(), actor=request.user)
        self.message_user(request, f"{year} is now the current academic year.")


@admin.register(ClassGroup)
class ClassGroupAdmin(SchoolScopedAdmin):
    list_display = ["name", "grade", "academic_year", "status"]
    list_filter = ["academic_year", "grade__stage", "status"]
    list_select_related = ["grade", "academic_year"]


@admin.register(Subject)
class SubjectAdmin(SchoolScopedAdmin):
    list_display = ["name", "code", "stage", "status"]
    list_filter = ["stage", "status"]


@admin.register(ClassSubject)
class ClassSubjectAdmin(AuditedAdmin):
    list_display = ["class_group", "subject", "status"]
    list_filter = ["class_group__academic_year", "class_group__grade", "status"]
    list_select_related = ["class_group", "subject"]
