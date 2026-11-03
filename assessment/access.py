"""Who may enter, correct and view marks and competencies (slice 3 design §8)."""
from django.utils import timezone

from attendance.access import is_class_teacher
from staffing.access import class_ids, class_subject_ids, sees_whole_school


def can_enter_marks(user, class_subject):
    """Primary teacher of the class subject, or cover in force with the assessment_entry duty."""
    return (
        user.is_active and user.has_perm("assessment.enter_marks")
        and class_subject.pk in class_subject_ids(user, duty="assessment_entry")
    )


def can_correct_marks(user, class_subject):
    """Teachers correct their own class subjects (decision B8); office roles correct any, always with a reason."""
    return user.is_active and (user.has_perm("assessment.correct_marks") or can_enter_marks(user, class_subject))


def can_view_class_subject(user, class_subject):
    if not user.is_active or not user.has_perm("assessment.view_assessment"):
        return False
    return (
        sees_whole_school(user)
        or class_subject.pk in class_subject_ids(user)
        or class_subject.class_group_id in class_ids(user)
    )


def can_record_competencies(user, class_group):
    """Only the class teacher records competencies (decision B6)."""
    return (
        user.is_active and user.has_perm("assessment.record_competencies")
        and is_class_teacher(user, class_group, timezone.localdate())
    )


def can_correct_competencies(user, class_group):
    return user.is_active and (user.has_perm("assessment.correct_marks") or can_record_competencies(user, class_group))


def can_view_class(user, class_group):
    if not user.is_active or not user.has_perm("assessment.view_assessment"):
        return False
    return sees_whole_school(user) or class_group.pk in class_ids(user)


def teachable_class_subjects(user):
    """Class subjects this user enters marks for now."""
    from school.models import ClassSubject

    return ClassSubject.objects.filter(pk__in=class_subject_ids(user, duty="assessment_entry"), status="active")
