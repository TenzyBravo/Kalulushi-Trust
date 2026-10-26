import datetime

from django.contrib.auth.models import Group

from accounts import roles
from accounts.models import User
from core.models import School
from learners import services
from school.models import AcademicYear, ClassGroup, ClassSubject, Grade, Subject

PASSWORD = "Tested-pass-2026"


def school():
    return School.current()


def make_user(username, *role_names, staff_number=None, email=None):
    user = User.objects.create_user(username, PASSWORD, staff_number=staff_number or username.upper(), email=email,
                                    first_name=username.capitalize(), last_name="Test")
    user.groups.set(Group.objects.filter(name__in=role_names))
    return user


def admin_user():
    return make_user("sysadmin", roles.SYSTEM_ADMIN)


def year(name="2026", status="current"):
    y = int(name)
    return AcademicYear.objects.create(school=school(), name=name, start_date=datetime.date(y, 1, 1),
                                       end_date=datetime.date(y, 12, 31), status=status)


def grade(code):
    return Grade.objects.get(school=school(), code=code)


def make_class(code, stream="A", academic_year=None):
    return ClassGroup.objects.create(school=school(), academic_year=academic_year or AcademicYear.current(),
                                     grade=grade(code), stream=stream)


def subject(stage_code="1", name="Mathematics", code="MATH"):
    return Subject.objects.create(school=school(), stage=grade(stage_code).stage, name=name, code=code)


def class_subject(class_group, subj):
    return ClassSubject.objects.create(class_group=class_group, subject=subj)


def registration(class_group, first="Chilufya", last="Mwansa", dob=datetime.date(2016, 3, 14), phone="0971234567",
                 guardian="Mary Mwansa", start=datetime.date(2026, 1, 15), existing=None):
    return services.Registration(
        first_name=first, last_name=last, date_of_birth=dob, gender="F", class_group=class_group, start_date=start,
        guardian=services.GuardianInput(guardian, phone, "Mother", existing=existing),
    )


def register(actor, class_group, **kwargs):
    reason = kwargs.pop("override_reason", "")
    return services.register_learner(registration(class_group, **kwargs), actor, override_reason=reason)
