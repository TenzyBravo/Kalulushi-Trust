from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ["username", "first_name", "last_name", "role", "is_active"]
    list_filter = ["role", "is_active"]
    fieldsets = DjangoUserAdmin.fieldsets + (("School role", {"fields": ["role"]}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("School role", {"fields": ["first_name", "last_name", "role"]}),
    )
