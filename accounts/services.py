"""Staff account changes. Each runs in one transaction with its audit record."""
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit import log

from . import roles
from .models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment, User, UserSession

PROFILE_FIELDS = ["staff_number", "username", "email", "title", "first_name", "last_name", "phone", "is_active"]


def _check_roles_allowed(actor, role_names):
    allowed = set(roles.assignable_roles(actor))
    refused = set(role_names) - allowed
    if refused:
        raise PermissionDenied(f"You may not give the role(s): {', '.join(sorted(refused))}.")


@transaction.atomic
def create_staff(actor, *, role_names, password, **fields):
    if not actor.has_perm("accounts.add_user"):
        raise PermissionDenied
    if not role_names:
        raise ValidationError("Give the account at least one role.")
    _check_roles_allowed(actor, role_names)
    user = User(school_id=actor.school_id, **fields)
    user.set_password(password)
    user.full_clean(exclude=["password"])
    user.save()
    user.set_roles(role_names)
    log.record(
        "user.create",
        user,
        changes={**log.snapshot(user, PROFILE_FIELDS), "roles": sorted(role_names)},
        actor=actor,
    )
    return user


@transaction.atomic
def update_staff(actor, user, **fields):
    if not actor.has_perm("accounts.change_user") or not (actor.pk == user.pk or roles.can_manage(actor, user)):
        raise PermissionDenied
    before = log.snapshot(user, PROFILE_FIELDS)
    for name, value in fields.items():
        setattr(user, name, value)
    user.full_clean(exclude=["password"])
    user.save()
    changes = log.diff(before, log.snapshot(user, PROFILE_FIELDS))
    if changes:
        log.record("user.update", user, changes=changes, actor=actor)
    return user


@transaction.atomic
def set_roles(actor, user, role_names):
    if not role_names:
        raise ValidationError("Give the account at least one role.")
    if not roles.can_manage(actor, user):
        raise PermissionDenied("You may not change this account's roles.")
    _check_roles_allowed(actor, role_names)
    before = sorted(user.role_names())
    user.set_roles(role_names)
    if before != sorted(role_names):
        log.record("user.roles", user, changes={"roles": [before, sorted(role_names)]}, actor=actor)
    return user


def _end_sessions(user):
    keys = list(UserSession.objects.filter(user=user).values_list("session_key", flat=True))
    Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(user=user).delete()
    return len(keys)


@transaction.atomic
def deactivate(actor, user, reason):
    """Design §5, "Deactivation": all of this commits together or not at all."""
    if not actor.has_perm("accounts.deactivate_user") or not roles.can_manage(actor, user):
        raise PermissionDenied("You may not deactivate this account.")
    if not reason.strip():
        raise ValidationError("Give a reason for deactivating the account.")
    user = User.objects.select_for_update().get(pk=user.pk)
    if not user.is_active:
        raise ValidationError(f"{user} is already deactivated.")
    now = timezone.now()
    today = timezone.localdate()

    user.is_active = False
    user.deactivated_at = now
    user.deactivated_by = actor
    user.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"])

    sessions = _end_sessions(user)

    ended_assignments = 0
    for assignment in TeacherAssignment.objects.filter(teacher=user, status="active"):
        assignment.status = "ended"
        if assignment.end_date is None or assignment.end_date > today:
            assignment.end_date = max(today, assignment.start_date)
        assignment.save(update_fields=["status", "end_date"])
        ended_assignments += 1

    ended_class_teacher = 0
    for assignment in ClassTeacherAssignment.objects.filter(teacher=user).exclude(end_date__lt=today):
        assignment.end_date = max(today, assignment.start_date)
        assignment.save(update_fields=["end_date"])
        ended_class_teacher += 1

    revoked_covers = CoverAuthorization.objects.filter(
        cover_teacher=user, revoked_at__isnull=True, ends_at__gt=now
    ).update(revoked_at=now, revoked_by=actor)

    log.record(
        "user.deactivate",
        user,
        changes={
            "is_active": [True, False],
            "sessions_ended": sessions,
            "teacher_assignments_ended": ended_assignments,
            "class_teacher_assignments_ended": ended_class_teacher,
            "covers_revoked": revoked_covers,
        },
        reason=reason,
        actor=actor,
    )
    return user


@transaction.atomic
def revoke_session(actor, session):
    target = session.user
    if not (actor.pk == target.pk or actor.has_perm("accounts.deactivate_user") and roles.can_manage(actor, target)):
        raise PermissionDenied
    Session.objects.filter(session_key=session.session_key).delete()
    log.record(
        "user.session_revoke",
        target,
        changes={"ip_address": session.ip_address, "started": session.created_at},
        actor=actor,
    )
    session.delete()


@transaction.atomic
def revoke_cover(actor, cover, reason=""):
    if cover.revoked_at:
        return cover
    cover.revoked_at = timezone.now()
    cover.revoked_by = actor
    cover.save(update_fields=["revoked_at", "revoked_by"])
    log.record("cover.revoke", cover, reason=reason, actor=actor, school_id=cover.teacher_assignment.school_id)
    return cover
