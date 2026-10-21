from django.contrib import admin

from .models import Enrollment, GradeBand, GradingScale, TeachingAssignment


class GradeBandInline(admin.TabularInline):
    model = GradeBand
    extra = 0


@admin.register(GradingScale)
class GradingScaleAdmin(admin.ModelAdmin):
    list_display = ["name", "level", "is_active"]
    inlines = [GradeBandInline]


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ["student", "class_group", "academic_year"]
    list_filter = ["academic_year", "class_group__grade"]
    search_fields = ["student__first_name", "student__last_name", "student__admission_number"]
    autocomplete_fields = ["student"]


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ["teacher", "subject", "class_group"]
    list_filter = ["class_group__academic_year", "subject__level"]
