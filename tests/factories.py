"""Small builders for tests. Each returns saved objects with sensible defaults."""
import datetime
import itertools

from django.utils import timezone

from accounts import roles
from accounts.models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment, User
from core.context import acting_as
from learners import services
from school.models import AcademicYear, ClassSubject, EducationStage, Grade, School, SchoolClass, Subject

_seq = itertools.count(1)
PASSWORD = "a-strong-test-pass-91"


def school():
    obj, _ = School.objects.get_or_create(code="KLT", defaults={"name": "Kalulushi Trust School"})
    return obj


def stage(name="Primary", sequence=1):
    return EducationStage.objects.get_or_create(school=school(), name=name, defaults={"sequence": sequence})[0]


def grade(code="5", name=None, stage_name="Primary"):
    st = stage(stage_name, 1 if stage_name == "Primary" else 2)
    existing = Grade.objects.filter(school=school(), code=code).first()
    if existing:
        return existing
    return Grade.objects.create(school=school(), stage=st, code=code, name=name or f"Grade {code}", sequence=next(_seq))


def year(name=None, status="current", start=None):
    today = timezone.localdate()
    start = start or datetime.date(today.year, 1, 1)
    end = datetime.date(start.year, 12, 31)
    return AcademicYear.objects.create(school=school(), name=name or str(start.year), start_date=start, end_date=end, status=status)


def current_year():
    return AcademicYear.objects.filter(school=school(), status="current").first() or year()


def school_class(grade_code="5", stream="A", academic_year=None):
    academic_year = academic_year or current_year()
    return SchoolClass.objects.create(school=school(), academic_year=academic_year, grade=grade(grade_code), stream=stream)


def subject(code="MATH", name="Mathematics", stage_name="Primary"):
    existing = Subject.objects.filter(school=school(), code=code).first()
    return existing or Subject.objects.create(school=school(), stage=stage(stage_name), code=code, name=name)


def class_subject(school_class_obj, subject_obj=None):
    return ClassSubject.objects.create(school_class=school_class_obj, subject=subject_obj or subject())


def user(*role_names, username=None, **fields):
    n = next(_seq)
    u = User.objects.create_user(
        username=username or f"user{n}",
        password=PASSWORD,
        school=school(),
        staff_number=fields.pop("staff_number", f"S{n:04d}"),
        first_name=fields.pop("first_name", "Test"),
        last_name=fields.pop("last_name", f"User{n}"),
        **fields,
    )
    u.set_roles(list(role_names))
    return User.objects.get(pk=u.pk)  # fresh instance: no cached permissions


def admin():
    return user(roles.SYSTEM_ADMIN)


def head():
    return user(roles.HEADTEACHER)


def academic_admin():
    return user(roles.ACADEMIC_ADMIN)


def teacher():
    return user(roles.TEACHER)


def assign(teacher_user, cs, by, assignment_type="primary", start=None, end=None):
    return TeacherAssignment.objects.create(
        school=school(), teacher=teacher_user, class_subject=cs, assignment_type=assignment_type,
        start_date=start or timezone.localdate() - datetime.timedelta(days=30), end_date=end, assigned_by=by,
    )


def class_teacher(teacher_user, school_class_obj, by):
    return ClassTeacherAssignment.objects.create(
        school_class=school_class_obj, teacher=teacher_user,
        start_date=timezone.localdate() - datetime.timedelta(days=30), assigned_by=by,
    )


def cover(assignment, cover_teacher, by, duties=("attendance",), hours_from_now=(-1, 4)):
    now = timezone.now()
    return CoverAuthorization.objects.create(
        teacher_assignment=assignment, cover_teacher=cover_teacher,
        starts_at=now + datetime.timedelta(hours=hours_from_now[0]),
        ends_at=now + datetime.timedelta(hours=hours_from_now[1]),
        duties=list(duties), reason="Sick leave", authorized_by=by,
    )


def guardian_input(name="Mary Mwansa", phone="0971234567", role="primary", **kwargs):
    return services.GuardianInput(relationship=kwargs.pop("relationship", "Mother"), contact_role=role, full_name=name, phone=phone, **kwargs)


def learner(actor=None, school_class_obj=None, guardians=None, **fields):
    actor = actor or User.objects.filter(groups__name=roles.SYSTEM_ADMIN).first() or admin()
    n = next(_seq)
    defaults = {
        "first_name": f"Learner{n}",
        "last_name": f"Family{n}",
        "gender": "F",
        "date_of_birth": datetime.date(2015, 1, 1) + datetime.timedelta(days=n * 40),
    }
    defaults.update(fields)
    if guardians is None:
        guardians = [guardian_input(name=f"Guardian {n}", phone=f"097{n:07d}")]
    with acting_as(actor):
        return services.register(
            actor, school_class=school_class_obj or school_class_for_learners(), guardians=guardians, **defaults
        )


def school_class_for_learners():
    return SchoolClass.objects.filter(academic_year=current_year()).first() or school_class()
