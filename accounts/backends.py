from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class StaffLoginBackend(ModelBackend):
    """Accepts a staff number, username or email in the one sign-in field.

    Inherits ModelBackend.get_user, which returns no user once is_active is
    false, so a deactivated account is refused on its next request.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or password is None:
            return None
        User = get_user_model()
        identifier = username.strip()
        user = (
            User.objects.filter(username=identifier).first()  # citext: case-insensitive
            or User.objects.filter(email=identifier).first()
            or User.objects.filter(staff_number__iexact=identifier).first()
        )
        if user is None:
            User().set_password(password)  # same work as a real check, so timing reveals nothing
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
