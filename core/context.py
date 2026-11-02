"""Who is acting, from where, in which request. Read by the audit log."""
import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class RequestContext:
    actor: object = None
    ip_address: str | None = None
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


_current = contextvars.ContextVar("request_context", default=None)


def current():
    return _current.get() or RequestContext()


@contextmanager
def acting_as(actor=None, ip_address=None, request_id=None):
    """Set the context for a block, e.g. a management command or one bulk import."""
    token = _current.set(RequestContext(actor, ip_address, request_id or uuid.uuid4()))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if request.user.is_authenticated else None
        with acting_as(user, request.META.get("REMOTE_ADDR")):
            return self.get_response(request)
