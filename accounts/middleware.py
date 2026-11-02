import datetime

from django.utils import timezone

from .models import UserSession

TOUCH_EVERY = datetime.timedelta(minutes=5)


class SessionActivityMiddleware:
    """Keeps user_sessions.last_seen_at roughly current, without a write on every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.session.session_key:
            now = timezone.now()
            UserSession.objects.filter(
                session_key=request.session.session_key, last_seen_at__lt=now - TOUCH_EVERY
            ).update(last_seen_at=now)
        return self.get_response(request)
