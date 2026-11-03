"""Assessment setup in School setup: grading schemes, plans and weights, assessment types, competency frameworks.

Active schemes and frameworks are read-only (the database refuses changes too);
a change is made as a new version.
"""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from core.admin import AuditedAdmin
from core.models import School

from . import services
from .models import (
    AssessmentComponent, AssessmentPlan, AssessmentType, Competency, CompetencyFramework, CompetencyLevel, GradeBand,
    GradingScheme,
)


def _run(modeladmin, request, queryset, action, done):
    for obj in queryset:
        try:
            action(obj, request.user)
        except ValidationError as exc:
            modeladmin.message_user(request, f"{obj}: {' '.join(exc.messages)}", messages.ERROR)
        else:
            modeladmin.message_user(request, f"{obj}: {done}.", messages.SUCCESS)


class FrozenUnlessDraftInline(admin.TabularInline):
    """Editable only while the parent is a draft."""

    extra = 0
    can_delete = False

    def _editable(self, obj):
        return obj is None or obj.status == "draft"

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)


class SchoolOwnedAdmin(AuditedAdmin):
    exclude = ["school"]

    def save_model(self, request, obj, form, change):
        if not obj.school_id:
            obj.school = School.current()
        if hasattr(obj, "created_by_id") and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class GradeBandInline(FrozenUnlessDraftInline):
    model = GradeBand


@admin.register(GradingScheme)
class GradingSchemeAdmin(SchoolOwnedAdmin):
    list_display = ["name", "version", "stage", "status", "decimal_places", "missing_rule"]
    list_filter = ["stage", "status"]
    exclude = ["school", "created_by", "activated_by", "activated_at", "status", "version"]
    inlines = [GradeBandInline]
    actions = ["activate", "new_version"]

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.status == GradingScheme.DRAFT) and super().has_change_permission(request, obj)

    @admin.action(description="Activate the selected draft schemes (checks the bands cover 0–100)", permissions=["add"])
    def activate(self, request, queryset):
        _run(self, request, queryset, services.activate_scheme, "now active")

    @admin.action(description="Make a new draft version of the selected schemes", permissions=["add"])
    def new_version(self, request, queryset):
        _run(self, request, queryset, services.new_scheme_version, "new draft version created")


class ComponentInline(FrozenUnlessDraftInline):
    model = AssessmentComponent


@admin.register(AssessmentPlan)
class AssessmentPlanAdmin(SchoolOwnedAdmin):
    list_display = ["__str__", "term", "grading_scheme", "status", "weights"]
    list_filter = ["term__academic_year", "status", "stage"]
    exclude = ["school", "created_by", "activated_by", "activated_at", "status"]
    inlines = [ComponentInline]
    actions = ["activate"]

    @admin.display(description="Weights")
    def weights(self, obj):
        parts = [f"{c.name} {c.weight if c.weight is not None else '?'}%" for c in obj.components.all()]
        return ", ".join(parts) or "—"

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.status == AssessmentPlan.DRAFT) and super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if obj.term_id and not obj.academic_year_id:
            obj.academic_year = obj.term.academic_year
        super().save_model(request, obj, form, change)

    @admin.action(description="Activate the selected plans (weights must add up to 100%)", permissions=["add"])
    def activate(self, request, queryset):
        _run(self, request, queryset, services.activate_plan, "now active")

    def get_exclude(self, request, obj=None):
        return [*self.exclude, "academic_year"]


@admin.register(AssessmentType)
class AssessmentTypeAdmin(SchoolOwnedAdmin):
    list_display = ["name", "status"]


class CompetencyInline(FrozenUnlessDraftInline):
    model = Competency


class LevelInline(FrozenUnlessDraftInline):
    model = CompetencyLevel


@admin.register(CompetencyFramework)
class CompetencyFrameworkAdmin(SchoolOwnedAdmin):
    list_display = ["name", "version", "stage", "status"]
    list_filter = ["stage", "status"]
    exclude = ["school", "created_by", "activated_at", "status"]
    inlines = [CompetencyInline, LevelInline]
    actions = ["activate"]

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.status == CompetencyFramework.DRAFT) and super().has_change_permission(request, obj)

    @admin.action(description="Activate the selected framework (retires the stage's current one)", permissions=["add"])
    def activate(self, request, queryset):
        _run(self, request, queryset, services.activate_framework, "now active")
