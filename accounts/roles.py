"""Role -> auth group mapping.

Each role is an auth Group of the same name. Groups are rebuilt after every
migrate, so editing ROLE_PERMISSIONS and running migrate is all a change needs.
"""
from django.contrib.auth.models import Group, Permission

ADMIN = "ADMIN"
HEAD = "HEAD"
TEACHER = "TEACHER"

ROLE_CHOICES = [
    (ADMIN, "Admin"),
    (HEAD, "Head"),
    (TEACHER, "Teacher"),
]

# Apps that hold school setup and people data.
SCHOOL_APPS = ["accounts", "school", "people", "academics"]

# Roles that may sign in to the Django admin.
ADMIN_SITE_ROLES = {ADMIN, HEAD}

TEACHER_PERMISSIONS = [
    "people.view_student",
    "people.view_guardian",
    "school.view_classgroup",
    "school.view_subject",
    "academics.view_teachingassignment",
]


def group_name(role):
    return dict(ROLE_CHOICES)[role]


def permissions_for(role):
    perms = Permission.objects.select_related("content_type")
    if role == ADMIN:
        return perms.filter(content_type__app_label__in=SCHOOL_APPS)
    if role == HEAD:
        return perms.filter(
            content_type__app_label__in=SCHOOL_APPS, codename__startswith="view_"
        ).exclude(content_type__app_label="accounts")
    pairs = [p.split(".") for p in TEACHER_PERMISSIONS]
    query = Permission.objects.none()
    for app_label, codename in pairs:
        query = query | perms.filter(content_type__app_label=app_label, codename=codename)
    return query


def sync_role_groups(**kwargs):
    for role, _ in ROLE_CHOICES:
        group, _ = Group.objects.get_or_create(name=group_name(role))
        group.permissions.set(permissions_for(role))
