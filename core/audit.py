"""Write audit records in the same transaction as the change they describe.

The middleware fills in who, from where and which request; services call
record() inside their transaction.
"""
import contextvars
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction

from .models import AuditLog, School

_context = contextvars.ContextVar("audit_context", default=None)


class AuditContext:
    def __init__(self, actor=None, ip_address=None, request_id=None):
        self.actor = actor
        self.ip_address = ip_address
        self.request_id = request_id or uuid.uuid4()


def set_context(ctx):
    return _context.set(ctx)


def reset_context(token):
    _context.reset(token)


def current_context():
    return _context.get()


def _plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


def diff(before, after):
    """{"field": [old, new]} for every key whose value changed."""
    return {
        k: [_plain(before.get(k)), _plain(after.get(k))]
        for k in after
        if _plain(before.get(k)) != _plain(after.get(k))
    }


def snapshot(instance, fields):
    return {f: getattr(instance, f) for f in fields}


def record(action, obj=None, *, changes=None, reason="", actor=None, school=None, object_type=None, object_id=None):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Audit records must be written inside the change's transaction.")
    ctx = current_context() or AuditContext()
    if obj is not None:
        object_type = object_type or obj._meta.model_name
        object_id = object_id if object_id is not None else obj.pk
        school = school or getattr(obj, "school", None)
    actor = actor if actor is not None else ctx.actor
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None
    return AuditLog.objects.create(
        school=school or School.current(),
        actor=actor,
        action=action,
        object_type=object_type or "",
        object_id=object_id,
        changes={k: _plain(v) for k, v in (changes or {}).items()},
        reason=reason or "",
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
    )
