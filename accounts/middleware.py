from datetime import timedelta

from django.contrib.auth import logout
from django.utils import timezone

from .models import UserSession
from .signals import SESSION_MARKER

TOUCH_EVERY = timedelta(minutes=5)


class UserSessionMiddleware:
    """Keeps last_seen_at fresh, follows key changes, and ends any session an administrator revoked."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        key = request.session.session_key if hasattr(request, "session") else None
        if user is not None and user.is_authenticated and key:
            row = UserSession.objects.filter(pk=request.session.get(SESSION_MARKER), user=user).first()
            if row is None:
                logout(request)  # revoked by an administrator, or never tracked
            elif row.session_key != key:
                UserSession.objects.filter(pk=row.pk).update(session_key=key, last_seen_at=timezone.now())
            elif timezone.now() - row.last_seen_at > TOUCH_EVERY:
                UserSession.objects.filter(pk=row.pk).update(last_seen_at=timezone.now())
        return self.get_response(request)
