from django.contrib import admin

from .models import AcademicYear, ClassGroup, Grade, Level, SchoolSettings, Subject, Term


@admin.register(SchoolSettings)
class SchoolSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return super().has_add_permission(request) and not SchoolSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class TermInline(admin.TabularInline):
    model = Term
    extra = 0


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ["name", "start_date", "end_date", "is_current"]
    inlines = [TermInline]


class GradeInline(admin.TabularInline):
    model = Grade
    extra = 0


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display = ["name", "order"]
    inlines = [GradeInline]


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "level", "order"]
    list_filter = ["level"]


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "grade", "academic_year", "class_teacher"]
    list_filter = ["academic_year", "grade__level"]
    list_select_related = ["grade", "academic_year", "class_teacher"]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "level", "is_active"]
    list_filter = ["level", "is_active"]
