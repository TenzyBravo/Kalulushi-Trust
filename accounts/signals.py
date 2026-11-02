from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .models import UserSession


@receiver(user_logged_in, dispatch_uid="track_session_start")
def track_session_start(sender, request, user, **kwargs):
    if request is None or not request.session.session_key:
        return
    UserSession.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={
            "user": user,
            "ip_address": request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
        },
    )


@receiver(user_logged_out, dispatch_uid="track_session_end")
def track_session_end(sender, request, user, **kwargs):
    if request is not None and request.session.session_key:
        UserSession.objects.filter(session_key=request.session.session_key).delete()
