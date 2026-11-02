from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db import transaction
from django.dispatch import receiver

from .models import UserSession

SESSION_MARKER = "_tracked_session"


@receiver(user_logged_in)
def track_login(sender, request, user, **kwargs):
    if request is None or not hasattr(request, "session"):
        return
    with transaction.atomic():
        row, _ = UserSession.objects.update_or_create(
            session_key=request.session.session_key,
            defaults={
                "user": user,
                "ip_address": request.META.get("REMOTE_ADDR") or None,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
            },
        )
    # Follow the row, not the key: Django changes the key when a password changes.
    request.session[SESSION_MARKER] = row.pk


@receiver(user_logged_out)
def track_logout(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session") and request.session.session_key:
        UserSession.objects.filter(session_key=request.session.session_key).delete()

