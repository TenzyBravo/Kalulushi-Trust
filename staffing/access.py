"""The one access check every view and export uses (design §5).

Roles decide what kind of record someone may touch; this module decides
which classes a teacher may touch, from their assignments and live cover.
"""
from django.db.models import Q
from django.utils import timezone

from accounts import roles

from .models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment

SCHOOL_WIDE_ROLES = [roles.SYSTEM_ADMIN, roles.HEADTEACHER, roles.ACADEMIC_ADMIN]


def sees_whole_school(user):
    return user.is_active and (user.is_superuser or user.has_role(*SCHOOL_WIDE_ROLES))


def _open_on(day):
    return Q(start_date__lte=day) & (Q(end_date__isnull=True) | Q(end_date__gte=day))


def class_subject_ids(user, duty=None, at=None):
    """Class subjects the teacher may work on now. Backup assignments grant nothing by themselves."""
    at = at or timezone.now()
    day = timezone.localdate(at)
    own = TeacherAssignment.objects.filter(
        _open_on(day), teacher=user, assignment_type=TeacherAssignment.PRIMARY, status="active"
    ).values_list("class_subject_id", flat=True)
    covers = CoverAuthorization.objects.filter(
        cover_teacher=user, revoked_at__isnull=True, starts_at__lte=at, ends_at__gt=at
    )
    if duty:
        covers = covers.filter(duties__contains=[duty])
    covered = covers.values_list("teacher_assignment__class_subject_id", flat=True)
    return set(own) | set(covered)


def class_ids(user, at=None):
    """Classes whose learners the teacher may see: subject assignments, live cover, or class teacher."""
    at = at or timezone.now()
    day = timezone.localdate(at)
    from school.models import ClassSubject

    via_subjects = ClassSubject.objects.filter(id__in=class_subject_ids(user, at=at)).values_list("class_group_id", flat=True)
    via_class_teacher = ClassTeacherAssignment.objects.filter(_open_on(day), teacher=user).values_list("class_group_id", flat=True)
    return set(via_subjects) | set(via_class_teacher)


def visible_learners(user, queryset=None):
    from learners.models import Learner

    qs = queryset if queryset is not None else Learner.objects.all()
    if not user.is_active or not user.has_perm("learners.view_learner"):
        return qs.none()
    if sees_whole_school(user):
        return qs
    day = timezone.localdate()
    return qs.filter(
        Q(enrollments__class_group_id__in=class_ids(user))
        & Q(enrollments__status="active")
        & Q(enrollments__start_date__lte=day)
        & (Q(enrollments__end_date__isnull=True) | Q(enrollments__end_date__gte=day))
    ).distinct()


def can_view_learner(user, learner):
    return visible_learners(user).filter(pk=learner.pk).exists()


def can_view_support_notes(user, learner):
    """Sys Admin and Headteacher: all. A teacher: assigned classes only, and only if granted the permission."""
    if not user.has_perm("learners.view_support_notes"):
        return False
    return sees_whole_school(user) or can_view_learner(user, learner)
