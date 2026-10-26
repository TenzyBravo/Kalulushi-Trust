from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
from core import audit

from .models import DUTIES, ClassTeacherAssignment, CoverAuthorization, TeacherAssignment


def _require_teacher(user):
    if not user.is_active:
        raise ValidationError(f"{user} is deactivated.")
    if not user.has_role(roles.TEACHER, roles.HEADTEACHER, roles.ACADEMIC_ADMIN):
        raise ValidationError(f"{user} does not hold a teaching role.")


@transaction.atomic
def assign_teacher(class_subject, teacher, assignment_type, start_date, actor, end_date=None):
    _require_teacher(teacher)
    if class_subject.status != "active":
        raise ValidationError("This subject has been dropped from the class.")
    year = class_subject.class_group.academic_year
    if not year.contains(start_date):
        raise ValidationError(f"The start date must fall inside academic year {year}.")
    overlapping = TeacherAssignment.objects.filter(class_subject=class_subject, status="active").filter(
        start_date__lte=end_date or year.end_date
    ).exclude(end_date__lt=start_date)
    if assignment_type == TeacherAssignment.PRIMARY and overlapping.filter(assignment_type=TeacherAssignment.PRIMARY).exists():
        raise ValidationError("This class subject already has a primary teacher for that period. End that assignment first.")
    if overlapping.filter(teacher=teacher).exists():
        raise ValidationError(f"{teacher} is already assigned to this class subject for that period.")
    assignment = TeacherAssignment.objects.create(
        school=class_subject.class_group.school, teacher=teacher, class_subject=class_subject,
        assignment_type=assignment_type, start_date=start_date, end_date=end_date, assigned_by=actor,
    )
    audit.record("teacher_assignment.create", assignment, actor=actor,
                 changes={"teacher": [None, str(teacher)], "class_subject": [None, str(class_subject)],
                          "type": [None, assignment_type], "start_date": [None, start_date]})
    return assignment


@transaction.atomic
def end_assignment(assignment, actor, end_date=None, reason=""):
    assignment = TeacherAssignment.objects.select_for_update().get(pk=assignment.pk)
    if assignment.status == "ended":
        return assignment
    end_date = max(end_date or timezone.localdate(), assignment.start_date)
    assignment.end_date, assignment.status = end_date, "ended"
    assignment.save(update_fields=["end_date", "status"])
    for cover in assignment.covers.filter(revoked_at__isnull=True, ends_at__gt=timezone.now()):
        revoke_cover(cover, actor, reason="Primary assignment ended")
    audit.record("teacher_assignment.end", assignment, actor=actor, reason=reason,
                 changes={"status": ["active", "ended"], "end_date": [None, end_date]})
    return assignment


@transaction.atomic
def assign_class_teacher(class_group, teacher, start_date, actor):
    _require_teacher(teacher)
    year = class_group.academic_year
    if not year.contains(start_date):
        raise ValidationError(f"The start date must fall inside academic year {year}.")
    current = class_group.class_teachers.select_for_update().filter(end_date__isnull=True).first()
    if current:
        if start_date <= current.start_date:
            raise ValidationError(f"{current.teacher} has been class teacher since {current.start_date}; pick a later date.")
        end_class_teacher(current, actor, end_date=start_date - timedelta(days=1))
    assignment = ClassTeacherAssignment.objects.create(class_group=class_group, teacher=teacher, start_date=start_date, assigned_by=actor)
    audit.record("class_teacher.assign", assignment, school=class_group.school, actor=actor,
                 changes={"class": [None, class_group.name], "teacher": [None, str(teacher)]})
    return assignment


@transaction.atomic
def end_class_teacher(assignment, actor, end_date=None):
    end_date = max(end_date or timezone.localdate(), assignment.start_date)
    assignment.end_date = end_date
    assignment.save(update_fields=["end_date"])
    audit.record("class_teacher.end", assignment, school=assignment.class_group.school, actor=actor,
                 changes={"end_date": [None, end_date]})
    return assignment


@transaction.atomic
def authorize_cover(primary, cover_teacher, starts_at, ends_at, duties, reason, actor):
    """Cover goes only to a named backup for this class subject, for a set window and set duties."""
    if primary.assignment_type != TeacherAssignment.PRIMARY or primary.status != "active":
        raise ValidationError("Cover can only be given for an active primary assignment.")
    if cover_teacher.pk == primary.teacher_id:
        raise ValidationError("The cover teacher must be someone other than the primary teacher.")
    if not cover_teacher.is_active:
        raise ValidationError(f"{cover_teacher} is deactivated.")
    is_backup = TeacherAssignment.objects.filter(
        class_subject=primary.class_subject, teacher=cover_teacher,
        assignment_type=TeacherAssignment.BACKUP, status="active",
    ).exists()
    if not is_backup:
        raise ValidationError(f"{cover_teacher} is not a backup teacher for {primary.class_subject}. Assign them as backup first.")
    duties = [d for d in DUTIES if d in set(duties)]
    if not duties:
        raise ValidationError("Choose at least one duty.")
    if ends_at <= starts_at:
        raise ValidationError("The cover must end after it starts.")
    if not reason.strip():
        raise ValidationError("A reason is required.")
    cover = CoverAuthorization.objects.create(
        teacher_assignment=primary, cover_teacher=cover_teacher, starts_at=starts_at, ends_at=ends_at,
        duties=duties, reason=reason.strip(), authorized_by=actor,
    )
    audit.record("cover.authorize", cover, school=primary.school, actor=actor, reason=reason,
                 changes={"cover_teacher": [None, str(cover_teacher)], "class_subject": [None, str(primary.class_subject)],
                          "window": [None, [starts_at, ends_at]], "duties": [None, duties]})
    return cover


@transaction.atomic
def revoke_cover(cover, actor, reason=""):
    cover = CoverAuthorization.objects.select_for_update().get(pk=cover.pk)
    if cover.revoked_at:
        return cover
    cover.revoked_at, cover.revoked_by = timezone.now(), actor
    cover.save(update_fields=["revoked_at", "revoked_by"])
    audit.record("cover.revoke", cover, school=cover.teacher_assignment.school, actor=actor, reason=reason)
    return cover
