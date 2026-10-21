import datetime

from accounts import roles
from accounts.models import User
from people.models import Guardian, Staff, Student, StudentGuardian
from school.models import AcademicYear, ClassGroup, Grade, Level, Subject


def make_user(username="user", role=roles.TEACHER, password="pass-12345!"):
    return User.objects.create_user(username=username, password=password, role=role)


def make_year(name="2026", current=True):
    y = int(name)
    return AcademicYear.objects.create(
        name=name, start_date=datetime.date(y, 1, 1), end_date=datetime.date(y, 12, 31), is_current=current
    )


def make_structure():
    """Primary (Grades 1-7) and Secondary (Grade 8) with classes 2A, 4A, 8A in 2026."""
    primary = Level.objects.create(name="Primary", order=1)
    secondary = Level.objects.create(name="Secondary", order=2)
    grades = {n: Grade.objects.create(level=primary, name=f"Grade {n}", code=str(n), order=n) for n in range(1, 8)}
    grades[8] = Grade.objects.create(level=secondary, name="Grade 8", code="8", order=8)
    year = make_year()
    classes = {
        f"{n}A": ClassGroup.objects.create(grade=grades[n], academic_year=year, stream="A") for n in (2, 4, 8)
    }
    return {"primary": primary, "secondary": secondary, "grades": grades, "year": year, "classes": classes}


def make_student(adm="2026-001", first="Chilufya", last="Mwansa", gender="F"):
    return Student.objects.create(admission_number=adm, first_name=first, last_name=last, gender=gender)


def make_guardian(phone="0971234567", name="Mary Mwansa"):
    return Guardian.objects.create(full_name=name, phone=phone)


def link(student, guardian, primary=False, receives_results=True):
    return StudentGuardian.objects.create(
        student=student, guardian=guardian, is_primary=primary, receives_results=receives_results
    )


def make_staff(number="T001", user=None):
    return Staff.objects.create(staff_number=number, first_name="Grace", last_name="Phiri", user=user)


def make_subject(level, name="Mathematics", code="MATH"):
    return Subject.objects.create(level=level, name=name, code=code)
