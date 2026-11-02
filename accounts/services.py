from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core import audit

from . import roles
from .models import User, UserSession

PROFILE_FIELDS = ["staff_number", "username", "email", "title", "first_name", "last_name", "phone"]


def _check_roles(actor, wanted):
    allowed = roles.assignable_roles(actor)
    refused = [r for r in wanted if r not in allowed]
    if refused:
        raise PermissionDenied(f"You may not give the role(s): {', '.join(refused)}.")
    if not wanted:
        raise ValidationError("Choose at least one role.")


@transaction.atomic
def create_staff(actor, roles_wanted, password, **profile):
    if not actor.has_perm("accounts.manage_staff"):
        raise PermissionDenied("You may not create staff accounts.")
    _check_roles(actor, roles_wanted)
    user = User.objects.create_user(profile.pop("username"), password, school=actor.school, **profile)
    user.groups.set(Group.objects.filter(name__in=roles_wanted))
    audit.record("user.create", user, actor=actor,
                 changes={**{f: [None, getattr(user, f)] for f in PROFILE_FIELDS if getattr(user, f)},
                          "roles": [None, sorted(roles_wanted)]})
    return user


@transaction.atomic
def update_staff(actor, user, roles_wanted=None, **profile):
    if not actor.has_perm("accounts.manage_staff") or not roles.can_manage(actor, user):
        raise PermissionDenied("You may not change this account.")
    user = User.objects.select_for_update().get(pk=user.pk)
    before = audit.snapshot(user, PROFILE_FIELDS)
    for f, v in profile.items():
        if f in PROFILE_FIELDS:
            setattr(user, f, v)
    user.save()
    changes = audit.diff(before, audit.snapshot(user, PROFILE_FIELDS))
    if changes:
        audit.record("user.update", user, changes=changes, actor=actor)
    if roles_wanted is not None:
        _check_roles(actor, roles_wanted)
        old = sorted(user.role_names)
        if old != sorted(roles_wanted):
            user.groups.remove(*user.groups.filter(name__in=roles.ALL_ROLES))
            user.groups.add(*Group.objects.filter(name__in=roles_wanted))
            audit.record("user.roles_change", user, changes={"roles": [old, sorted(roles_wanted)]}, actor=actor)
    return user


@transaction.atomic
def set_password(actor, user, password):
    if not roles.can_manage(actor, user):
        raise PermissionDenied("You may not change this account.")
    user.set_password(password)
    user.save(update_fields=["password"])
    _end_sessions(user)
    audit.record("user.password_reset", user, actor=actor)


def _end_sessions(user, keep=None):
    rows = UserSession.objects.filter(user=user)
    if keep:
        rows = rows.exclude(session_key=keep)
    keys = list(rows.values_list("session_key", flat=True))
    Session.objects.filter(session_key__in=keys).delete()
    rows.delete()
    return keys


@transaction.atomic
def revoke_session(actor, session):
    if not (actor.pk == session.user_id or roles.can_manage(actor, session.user)):
        raise PermissionDenied("You may not end this session.")
    Session.objects.filter(session_key=session.session_key).delete()
    audit.record("user.session_revoke", session.user, actor=actor, changes={"session": [session.ip_address, None]})
    session.delete()


@transaction.atomic
def deactivate(actor, user, reason):
    """Revoke access immediately and end everything the account held, in one transaction (design §5)."""
    from staffing.models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment

    if not actor.has_perm("accounts.deactivate_staff") or not roles.can_manage(actor, user):
        raise PermissionDenied("You may not deactivate this account.")
    if not reason.strip():
        raise ValidationError("A reason is required.")
    user = User.objects.select_for_update().get(pk=user.pk)
    if not user.is_active:
        return user
    now, today = timezone.now(), timezone.localdate()
    user.is_active, user.deactivated_at, user.deactivated_by = False, now, actor
    user.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"])
    sessions = _end_sessions(user)

    open_assignments = TeacherAssignment.objects.filter(teacher=user, status="active")
    ended = list(open_assignments.values_list("id", flat=True))
    for a in open_assignments:
        a.status, a.end_date = "ended", max(today, a.start_date)
        a.save(update_fields=["status", "end_date"])
    class_teacher = ClassTeacherAssignment.objects.filter(teacher=user, end_date__isnull=True)
    ended_ct = list(class_teacher.values_list("id", flat=True))
    for c in class_teacher:
        c.end_date = max(today, c.start_date)
        c.save(update_fields=["end_date"])
    covers = CoverAuthorization.objects.filter(revoked_at__isnull=True, ends_at__gt=now).filter(
        Q(cover_teacher=user) | Q(teacher_assignment__teacher=user)
    )
    revoked = list(covers.values_list("id", flat=True))
    covers.update(revoked_at=now, revoked_by=actor)

    audit.record("user.deactivate", user, actor=actor, reason=reason, changes={
        "is_active": [True, False], "sessions_ended": [None, len(sessions)],
        "teacher_assignments_ended": [None, ended], "class_teacher_ended": [None, ended_ct],
        "covers_revoked": [None, revoked],
    })
    return user


@transaction.atomic
def reactivate(actor, user, reason):
    if not actor.has_perm("accounts.deactivate_staff") or not roles.can_manage(actor, user):
        raise PermissionDenied("You may not reactivate this account.")
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.is_active:
        return user
    user.is_active, user.deactivated_at, user.deactivated_by = True, None, None
    user.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"])
    audit.record("user.reactivate", user, actor=actor, reason=reason, changes={"is_active": [False, True]})
    return user
