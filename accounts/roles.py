"""Roles and what each may do (design §5, "Roles and permissions").

Each role is an auth Group. sync_role_groups() runs after every migrate, so
changing a list here and running migrate is the whole change, reviewed like
any other code. Which classes a teacher may touch is decided separately, by
accounts.access.
"""
from django.contrib.auth.models import Group, Permission

SYSTEM_ADMIN = "System Administrator"
HEADTEACHER = "Headteacher"
ACADEMIC_ADMIN = "Academic Administrator"
TEACHER = "Teacher"

ALL_ROLES = [SYSTEM_ADMIN, HEADTEACHER, ACADEMIC_ADMIN, TEACHER]

# Roles that use the Django admin for school structure, assignments and cover.
ADMIN_SITE_ROLES = {SYSTEM_ADMIN, HEADTEACHER, ACADEMIC_ADMIN}

# Decision D6: a Headteacher may create and deactivate only these roles.
HEADTEACHER_MANAGES = [ACADEMIC_ADMIN, TEACHER]

_STRUCTURE = ["schoolsettings", "educationstage", "grade", "academicyear", "term", "schoolclass", "subject", "classsubject"]
_ASSIGNMENTS = ["teacherassignment", "classteacherassignment", "coverauthorization"]
_LEARNER_RECORDS = ["learner", "guardian", "guardiancontact", "learnerguardian", "enrollment"]


def _perms(actions, models):
    return [f"{action}_{model}" for action in actions for model in models]


ROLE_PERMISSIONS = {
    SYSTEM_ADMIN: [
        *_perms(["add", "change", "view"], _STRUCTURE),
        "view_school",
        "add_user", "change_user", "view_user", "deactivate_user", "view_usersession",
        *_perms(["add", "change", "view"], _ASSIGNMENTS),
        *_perms(["add", "change", "view"], _LEARNER_RECORDS),
        "view_all_learners", "override_duplicate",
        "add_learnersupportnote", "change_learnersupportnote", "view_learnersupportnote",
        "view_auditlog",
    ],
    HEADTEACHER: [
        *_perms(["view"], _STRUCTURE),
        "view_school",
        "add_user", "change_user", "view_user", "deactivate_user", "view_usersession",
        *_perms(["add", "change", "view"], _ASSIGNMENTS),
        *_perms(["add", "change", "view"], _LEARNER_RECORDS),
        "view_all_learners", "override_duplicate",
        "add_learnersupportnote", "change_learnersupportnote", "view_learnersupportnote",
        "view_auditlog",
    ],
    ACADEMIC_ADMIN: [
        *_perms(["add", "change", "view"], _STRUCTURE),
        "view_school",
        "view_user",
        *_perms(["add", "change", "view"], _ASSIGNMENTS),
        *_perms(["add", "change", "view"], _LEARNER_RECORDS),
        "view_all_learners", "override_duplicate",
    ],
    TEACHER: [
        *_perms(["view"], ["academicyear", "term", "grade", "schoolclass", "subject", "classsubject"]),
        *_perms(["view"], _LEARNER_RECORDS),
    ],
}

# No role is granted a delete permission (design §2, "No routine deletion").
assert not any(p.startswith("delete_") for perms in ROLE_PERMISSIONS.values() for p in perms)

APPS = ["school", "accounts", "learners", "audit"]


def assignable_roles(user):
    """Roles this user may give to, or take from, another account."""
    if user.is_superuser or user.has_role(SYSTEM_ADMIN):
        return list(ALL_ROLES)
    if user.has_role(HEADTEACHER):
        return list(HEADTEACHER_MANAGES)
    return []


def can_manage(actor, target):
    """Whether actor may change target's roles or deactivate them (decision D6)."""
    if actor.pk == target.pk or target.is_superuser and not actor.is_superuser:
        return False
    allowed = set(assignable_roles(actor))
    return bool(allowed) and set(target.role_names()) <= allowed


def sync_role_groups(**kwargs):
    for role, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        perms = Permission.objects.filter(content_type__app_label__in=APPS, codename__in=codenames)
        missing = set(codenames) - set(perms.values_list("codename", flat=True))
        if missing:
            raise RuntimeError(f"Unknown permissions for {role}: {sorted(missing)}")
        group.permissions.set(perms)
