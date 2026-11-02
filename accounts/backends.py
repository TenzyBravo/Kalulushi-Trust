from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from .models import User


class StaffLoginBackend(ModelBackend):
    """Sign in with staff number, username or email in one field (design §5).

    Inherits ModelBackend.get_user, which refuses inactive users, so a
    deactivated account is rejected on its next request.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (username or kwargs.get(User.USERNAME_FIELD) or "").strip()
        if not identifier or password is None:
            return None
        matches = list(
            User.objects.filter(Q(username=identifier) | Q(email=identifier) | Q(staff_number__iexact=identifier))[:3]
        )
        # Prefer an exact username, then staff number, then email; refuse anything ambiguous.
        for pick in (
            [u for u in matches if u.username.lower() == identifier.lower()],
            [u for u in matches if u.staff_number.lower() == identifier.lower()],
            [u for u in matches if (u.email or "").lower() == identifier.lower()],
        ):
            if len(pick) == 1:
                user = pick[0]
                if user.check_password(password) and self.user_can_authenticate(user):
                    return user
                return None
            if len(pick) > 1:
                return None
        User().set_password(password)  # same work as a real check, against timing probes
        return None
