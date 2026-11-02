"""Writing audit records.

Call record() inside the same transaction as the change it describes, so a
change can never commit without its audit record (design §7).
"""
import datetime
import decimal
import uuid

from django.db import connection, models

from core import context

from .models import AuditLog


def jsonable(value):
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, (datetime.date, datetime.datetime, uuid.UUID, decimal.Decimal)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool, dict)):
        return value
    return str(value)


def snapshot(obj, fields):
    return {f: jsonable(getattr(obj, f)) for f in fields}


def diff(before, after):
    """{"field": [old, new]} for every field that changed."""
    return {f: [before.get(f), after.get(f)] for f in after if before.get(f) != after.get(f)}


def _school_id(obj, actor):
    for candidate in (obj, actor):
        if candidate is not None and getattr(candidate, "school_id", None):
            return candidate.school_id
    return None


def record(action, obj=None, *, changes=None, reason="", actor=None, school_id=None):
    if not connection.in_atomic_block:
        raise RuntimeError(f"Audit record for {action} must be written inside the change's transaction.")
    ctx = context.current()
    actor = actor or ctx.actor
    return AuditLog.objects.create(
        school_id=school_id or _school_id(obj, actor),
        actor=actor,
        action=action,
        object_type=obj._meta.model_name if obj is not None else "",
        object_id=obj.pk if obj is not None else None,
        changes={k: jsonable(v) for k, v in (changes or {}).items()},
        reason=reason or "",
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
    )
