import uuid

from . import audit


class AuditContextMiddleware:
    """Gives every request one request_id and makes the actor and IP available to audit.record()."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR")
        user = getattr(request, "user", None)
        token = audit.set_context(
            audit.AuditContext(
                actor=user if user is not None and user.is_authenticated else None,
                ip_address=ip or None,
                request_id=uuid.uuid4(),
            )
        )
        try:
            return self.get_response(request)
        finally:
            audit.reset_context(token)
