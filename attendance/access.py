"""Who may take, correct or view a register, and work on an absence case (design §4.1, §6)."""
from django.db.models import Q
from django.utils import timezone

from core.models import SchoolSettings
from staffing.access import class_ids, sees_whole_school
from staffing.models import ClassTeacherAssignment

from .calendar import for_days
from .models import AbsenceCase

EDIT, CORRECT, VIEW = "edit", "correct", "view"


def class_teacher_on(class_group, day):
    assignment = (
        ClassTeacherAssignment.objects.filter(class_group=class_group, start_date__lte=day)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).select_related("teacher").first()
    )
    return assignment.teacher if assignment else None


def is_class_teacher(user, class_group, day):
    teacher = class_teacher_on(class_group, day)
    return teacher is not None and teacher.pk == user.pk


def register_access(user, class_group, day, session_exists, today=None):
    """EDIT for the class teacher inside the window, CORRECT for office roles, VIEW, or None."""
    today = today or timezone.localdate()
    if not user.is_active:
        return None
    if day <= today and user.has_perm("attendance.take_register") and is_class_teacher(user, class_group, day):
        settings = SchoolSettings.for_school(class_group.school)
        limit = settings.attendance_edit_days if session_exists else settings.attendance_backfill_days
        if len(for_days(day, today, class_group.school).school_days_between(day, today)) <= limit:
            return EDIT
    if day <= today and user.has_perm("attendance.correct_attendance"):
        return CORRECT
    if sees_whole_school(user) or class_group.pk in class_ids(user):
        return VIEW
    return None


def register_classes(user, day=None):
    """Classes this user is class teacher of on the day."""
    day = day or timezone.localdate()
    from school.models import ClassGroup

    ids = ClassTeacherAssignment.objects.filter(teacher=user, start_date__lte=day).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=day)
    ).values_list("class_group_id", flat=True)
    return ClassGroup.objects.filter(pk__in=ids, status="active").select_related("grade", "academic_year")


def visible_cases(user):
    if not user.is_active or not user.has_perm("attendance.view_absencecase"):
        return AbsenceCase.objects.none()
    if sees_whole_school(user):
        return AbsenceCase.objects.all()
    return AbsenceCase.objects.filter(Q(assigned_teacher=user) | Q(class_group_id__in=class_ids(user)))


def can_work_on_case(user, case):
    if not user.is_active or not user.has_perm("attendance.change_absencecase"):
        return False
    if sees_whole_school(user):
        return True
    return case.assigned_teacher_id == user.pk or is_class_teacher(user, case.class_group, timezone.localdate())


def can_review_escalations(user):
    return user.is_active and user.has_perm("attendance.review_escalations")
