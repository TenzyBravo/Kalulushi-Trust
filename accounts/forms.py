from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from core.phone import normalise_phone

from . import roles
from .models import User


class StaffForm(forms.Form):
    staff_number = forms.CharField()
    username = forms.CharField()
    email = forms.EmailField(required=False)
    title = forms.CharField(required=False)
    first_name = forms.CharField()
    last_name = forms.CharField()
    phone = forms.CharField(required=False, help_text="e.g. 0971234567")
    roles = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=[])
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text="Initial password. Share it privately; the person can change it after signing in.")

    def __init__(self, *args, actor=None, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        self.fields["roles"].choices = [(r, r) for r in roles.assignable_roles(actor)]
        if instance is None:
            self.fields["password"].required = True
        else:
            del self.fields["password"]

    def clean_phone(self):
        value = self.cleaned_data["phone"]
        if not value:
            return ""
        try:
            return normalise_phone(value)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)

    def _unique(self, field, **lookup):
        qs = User.objects.filter(**lookup)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Another account already uses this {field}.")

    def clean_username(self):
        v = self.cleaned_data["username"].strip()
        self._unique("username", username=v)
        return v

    def clean_staff_number(self):
        v = self.cleaned_data["staff_number"].strip()
        self._unique("staff number", staff_number__iexact=v)
        return v

    def clean_email(self):
        v = (self.cleaned_data["email"] or "").strip()
        if v:
            self._unique("email", email=v)
        return v

    def clean_password(self):
        v = self.cleaned_data.get("password")
        if v:
            validate_password(v)
        return v


class PasswordResetForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="New password")

    def clean_password(self):
        v = self.cleaned_data["password"]
        validate_password(v)
        return v


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
