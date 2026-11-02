from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from .models import User


class SignInForm(AuthenticationForm):
    username = forms.CharField(label="Staff number, username or email", widget=forms.TextInput(attrs={"autofocus": True}))
    error_messages = {
        "invalid_login": "Those details didn't match an active account. Check them and try again.",
        "inactive": "This account has been deactivated.",
    }


class StaffForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["staff_number", "title", "first_name", "last_name", "username", "email", "phone"]
        help_texts = {"username": "", "phone": "Optional, e.g. 0971234567"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name"):
            self.fields[name].required = True

    def clean_email(self):
        return self.cleaned_data.get("email") or None


def role_field(allowed, initial=()):
    return forms.MultipleChoiceField(
        choices=[(r, r) for r in allowed],
        initial=list(initial),
        widget=forms.CheckboxSelectMultiple,
        label="Roles",
    )


class NewStaffForm(StaffForm):
    password = forms.CharField(
        widget=forms.PasswordInput, help_text="A starting password. Ask them to change it after signing in."
    )

    def __init__(self, *args, allowed_roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"] = role_field(allowed_roles)

    def clean(self):
        data = super().clean()
        if data.get("password"):
            candidate = User(**{f: data.get(f) for f in ("username", "first_name", "last_name", "email")})
            try:
                validate_password(data["password"], candidate)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return data


class RolesForm(forms.Form):
    def __init__(self, *args, allowed_roles, current, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"] = role_field(allowed_roles, current)


class DeactivateForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
