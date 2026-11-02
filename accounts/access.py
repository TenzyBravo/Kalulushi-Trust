"""Which classes and learners a user may touch (design §5).

Role permissions say what kind of record a user may touch; this module says
which ones. Every view and export goes through these functions.

A teacher reaches a class through:
  - an active primary teacher assignment on one of its class subjects,
  - a cover authorization in force for one of those assignments (limited to
    its duties), or
  - being its class teacher.
A backup assignment grants nothing by itself.
"""
from django.db.models import Q
from django.utils import timezone

from learners.models import Learner
from school.models import SchoolClass

from .models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment


def sees_all_learners(user):
    return user.is_active and user.has_perm("learners.view_all_learners")


def accessible_classes(user, duty=None):
    """Classes the user may work with. duty limits cover to authorizations that include it."""
    if not user.is_authenticated or not user.is_active:
        return SchoolClass.objects.none()
    if sees_all_learners(user):
        return SchoolClass.objects.filter(school_id=user.school_id)

    today = timezone.localdate()
    own = TeacherAssignment.in_force(today).filter(teacher=user, assignment_type="primary")
    covers = CoverAuthorization.in_force().filter(
        cover_teacher=user, teacher_assignment__status="active", teacher_assignment__assignment_type="primary"
    )
    if duty:
        covers = covers.filter(duties__contains=[duty])
    led = ClassTeacherAssignment.in_force(today).filter(teacher=user)

    q = Q(class_subjects__teacher_assignments__in=own) | Q(
        class_subjects__teacher_assignments__cover_authorizations__in=covers
    )
    if duty is None:
        # Leading a class means seeing its learners; duties such as attendance come with slice 2.
        q |= Q(class_teacher_assignments__in=led)
    return SchoolClass.objects.filter(q, school_id=user.school_id).distinct()


def can_access_class(user, school_class, duty=None):
    return accessible_classes(user, duty).filter(pk=school_class.pk).exists()


def visible_learners(user):
    if not user.is_authenticated or not user.is_active or not user.has_perm("learners.view_learner"):
        return Learner.objects.none()
    learners = Learner.objects.filter(school_id=user.school_id)
    if sees_all_learners(user):
        return learners
    today = timezone.localdate()
    return learners.filter(
        Q(enrollments__end_date__isnull=True) | Q(enrollments__end_date__gte=today),
        enrollments__start_date__lte=today,
        enrollments__status="active",
        enrollments__school_class__in=accessible_classes(user),
    ).distinct()


def can_view_learner(user, learner):
    return visible_learners(user).filter(pk=learner.pk).exists()


def can_view_support_notes(user, learner):
    """Headteacher and System Administrator see all. A teacher needs the permission granted
    to them individually and the learner in one of their classes. Academic Administrators do not."""
    if not user.has_perm("learners.view_learnersupportnote"):
        return False
    return can_view_learner(user, learner)
