from django.contrib import admin

from .models import Guardian, Staff, Student, StudentGuardian


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 0
    autocomplete_fields = ["guardian"]


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["admission_number", "last_name", "first_name", "gender", "is_active"]
    list_filter = ["is_active", "gender"]
    search_fields = ["admission_number", "first_name", "last_name"]
    inlines = [StudentGuardianInline]


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ["full_name", "phone", "email"]
    search_fields = ["full_name", "phone", "email"]


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ["staff_number", "last_name", "first_name", "user", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["staff_number", "first_name", "last_name"]
