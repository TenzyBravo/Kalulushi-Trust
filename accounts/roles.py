"""The four Version 1 roles and what each may do (design §5).

Each role is an auth Group. Groups are rebuilt from ROLE_PERMISSIONS after
every migrate, so a change here is reviewed like any other code. Which
classes a teacher may touch is decided separately, by staffing.access.
"""
SYSTEM_ADMIN = "System Administrator"
HEADTEACHER = "Headteacher"
ACADEMIC_ADMIN = "Academic Administrator"
TEACHER = "Teacher"

ALL_ROLES = [SYSTEM_ADMIN, HEADTEACHER, ACADEMIC_ADMIN, TEACHER]
ADMIN_SITE_ROLES = [SYSTEM_ADMIN, HEADTEACHER, ACADEMIC_ADMIN]

# D6: which roles each role may hand out (and which accounts it may manage).
ASSIGNABLE_ROLES = {
    SYSTEM_ADMIN: ALL_ROLES,
    HEADTEACHER: [TEACHER, ACADEMIC_ADMIN],
}

STRUCTURE = [
    "core.schoolsettings", "core.school",
    "school.educationstage", "school.grade", "school.academicyear", "school.term",
    "school.classgroup", "school.subject", "school.classsubject",
]
ASSIGNMENTS = ["staffing.teacherassignment", "staffing.classteacherassignment", "staffing.coverauthorization"]
LEARNER_RECORDS = [
    "learners.learner", "learners.guardian", "learners.guardiancontact",
    "learners.learnerguardian", "learners.enrollment", "learners.learnersupportnote",
]


def _model_perms(models, actions):
    return [f"{m.split('.')[0]}.{a}_{m.split('.')[1]}" for m in models for a in actions]


# No role gets a delete permission on learner, guardian, enrollment, user or audit records.
ROLE_PERMISSIONS = {
    SYSTEM_ADMIN: (
        _model_perms(STRUCTURE, ["add", "change", "view"])
        + _model_perms(ASSIGNMENTS, ["add", "change", "view"])
        + _model_perms(LEARNER_RECORDS, ["add", "change", "view"])
        + ["accounts.view_user", "accounts.manage_staff", "accounts.deactivate_staff",
           "learners.override_duplicate", "learners.view_support_notes", "learners.transfer_learner",
           "learners.import_learners", "core.view_audit_log"]
    ),
    HEADTEACHER: (
        _model_perms(STRUCTURE, ["view"])
        + _model_perms(ASSIGNMENTS, ["add", "change", "view"])
        + _model_perms(LEARNER_RECORDS, ["add", "change", "view"])
        + ["accounts.view_user", "accounts.manage_staff", "accounts.deactivate_staff",
           "learners.override_duplicate", "learners.view_support_notes", "learners.transfer_learner",
           "learners.import_learners", "core.view_audit_log"]
    ),
    ACADEMIC_ADMIN: (
        _model_perms(STRUCTURE, ["add", "change", "view"])
        + _model_perms(ASSIGNMENTS, ["add", "change", "view"])
        + _model_perms([m for m in LEARNER_RECORDS if m != "learners.learnersupportnote"], ["add", "change", "view"])
        + ["learners.add_learnersupportnote", "accounts.view_user",
           "learners.override_duplicate", "learners.import_learners"]
    ),
    # Teachers see learners only in their assigned classes (staffing.access).
    # Support notes need view_support_notes granted to the individual teacher.
    TEACHER: ["learners.view_learner", "learners.view_guardian", "learners.view_enrollment",
              "school.view_classgroup", "school.view_subject"],
}


def assignable_roles(actor):
    if actor.is_superuser:
        return list(ALL_ROLES)
    allowed = []
    for role in actor.role_names:
        allowed += [r for r in ASSIGNABLE_ROLES.get(role, []) if r not in allowed]
    return [r for r in ALL_ROLES if r in allowed]


def can_manage(actor, target):
    """D6: a Headteacher manages only Teacher and Academic Administrator accounts."""
    if actor.pk == target.pk:
        return False
    allowed = set(assignable_roles(actor))
    return bool(allowed) and set(target.role_names) <= allowed and not (target.is_superuser and not actor.is_superuser)


def sync_role_groups(**kwargs):
    from django.contrib.auth.models import Group, Permission

    for role, codes in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        perms = []
        for code in codes:
            app_label, codename = code.split(".")
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if perm:
                perms.append(perm)
        group.permissions.set(perms)
