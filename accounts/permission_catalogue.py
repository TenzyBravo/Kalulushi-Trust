"""Every permission the system uses, grouped by module, for the Permissions page.

What each role holds is defined in roles.py and changes only through code.
A few permissions can also be granted to one person on their staff page;
those are listed in GRANTABLE.
"""
from django.contrib.auth.models import Group, Permission
from django.db.models import Count, Q

from . import roles

MODULES = [
    ("learners", "Learners and guardians"),
    ("attendance", "Attendance and follow-up"),
    ("assessment", "Assessments and competencies"),
    ("staffing", "Teaching assignments and cover"),
    ("school", "School structure"),
    ("accounts", "Staff accounts"),
    ("core", "School settings and audit"),
]

# Shown with a shield: personal or medical information, or the record of who did what.
SENSITIVE = {"learners.view_support_notes", "learners.add_learnersupportnote", "learners.change_learnersupportnote",
             "core.view_audit_log"}

TYPE_OVERRIDES = {
    "accounts.manage_staff": "admin",
    "accounts.deactivate_staff": "admin",
    "learners.override_duplicate": "admin",
    "attendance.review_escalations": "admin",
    "learners.import_learners": "import",
    "learners.transfer_learner": "edit",
    "attendance.correct_attendance": "edit",
    "attendance.take_register": "create",
    "attendance.add_closure": "create",
    "assessment.enter_marks": "create",
    "assessment.record_competencies": "create",
    "assessment.correct_marks": "edit",
    "learners.request_transfer": "create",
    "learners.prepare_promotions": "create",
    "learners.approve_enrollment_requests": "admin",
}

DESCRIPTIONS = {
    "accounts.manage_staff": "Create staff accounts and give roles, within the role limits (D6).",
    "accounts.deactivate_staff": "Deactivate an account: signs them out and ends their assignments and cover.",
    "core.view_audit_log": "See every recorded change: who did what, when, and why.",
    "learners.view_support_notes": "Read a learner's medical and support notes. Every read is audited.",
    "learners.override_duplicate": "Save a learner despite a likely-duplicate warning, with a reason.",
    "learners.transfer_learner": "Carry out a transfer once it is approved (used by the Headteacher).",
    "learners.request_transfer": "Ask the Headteacher to move a learner they teach to another class.",
    "learners.prepare_promotions": "Prepare a class's year-end promotion sheet (class teacher, or office).",
    "learners.approve_enrollment_requests": "Approve or return promotion sheets and approve or reject transfers.",
    "learners.import_learners": "Register many learners at once from the Excel template.",
    "attendance.take_register": "Take the daily register, for a class they are class teacher of.",
    "attendance.correct_attendance": "Correct any class's register, with a reason.",
    "attendance.add_closure": "Close the school, a grade or a class (e.g. exam classes finishing early).",
    "attendance.review_escalations": "Review and close absence cases referred to the Headteacher.",
    "assessment.enter_marks": "Create assessments and enter marks, for class subjects they teach (or cover).",
    "assessment.record_competencies": "Record end-of-term competencies, for the class they are class teacher of.",
    "assessment.correct_marks": "Correct any submitted marks or competencies, with a reason.",
}

# Permissions that may be given to one person on top of their roles (design: "user_permissions").
GRANTABLE = [
    "learners.view_support_notes",
    "learners.import_learners",
    "attendance.correct_attendance",
    "attendance.add_closure",
    "core.view_audit_log",
]

TYPE_LABELS = {"view": "View", "create": "Create", "edit": "Edit", "import": "Import", "export": "Export",
               "admin": "Admin", "sensitive": "Sensitive"}


def code_of(permission):
    return f"{permission.content_type.app_label}.{permission.codename}"


def kind_of(code):
    if code in TYPE_OVERRIDES:
        return TYPE_OVERRIDES[code]
    codename = code.split(".", 1)[1]
    for prefix, kind in (("view_", "view"), ("add_", "create"), ("change_", "edit"), ("export", "export"), ("import", "import")):
        if codename.startswith(prefix):
            return kind
    return "admin"


def label_of(permission):
    name = permission.name.removeprefix("Can ")
    return name[:1].upper() + name[1:]


def catalogue():
    """[(module label, [row, ...])] with each permission's type, roles and individual holders."""
    groups = {g.name: set(code_of(p) for p in g.permissions.select_related("content_type"))
              for g in Group.objects.filter(name__in=roles.ALL_ROLES).prefetch_related("permissions")}
    permissions = (
        Permission.objects.filter(content_type__app_label__in=[m for m, _ in MODULES])
        .exclude(codename__startswith="delete_")  # no role may delete records (design §2)
        .select_related("content_type")
        .annotate(individuals=Count("user", filter=Q(user__is_active=True), distinct=True))
        .order_by("content_type__app_label", "codename")
    )
    by_module = {m: [] for m, _ in MODULES}
    for p in permissions:
        code = code_of(p)
        held_by = [r for r in roles.ALL_ROLES if code in groups.get(r, set())]
        by_module[p.content_type.app_label].append({
            "label": label_of(p),
            "code": code,
            "kind": kind_of(code),
            "kind_label": TYPE_LABELS[kind_of(code)],
            "sensitive": code in SENSITIVE,
            "description": DESCRIPTIONS.get(code, ""),
            "roles": held_by,
            "individuals": p.individuals,
            "grantable": code in GRANTABLE,
        })
    order = {"admin": 0, "create": 1, "edit": 2, "export": 3, "import": 4, "view": 5}
    return [
        (label, sorted(by_module[m], key=lambda r: (order.get(r["kind"], 9), r["label"])))
        for m, label in MODULES if by_module[m]
    ]


def get_permission(code):
    app_label, codename = code.split(".", 1)
    return Permission.objects.select_related("content_type").get(content_type__app_label=app_label, codename=codename)
