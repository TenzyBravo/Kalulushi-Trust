from django import forms
from django.core.exceptions import ValidationError

from core.phone import normalise_phone
from school.models import AcademicYear, ClassGroup

from .models import LearnerGuardian, LearnerSupportNote

DATE = forms.DateInput(attrs={"type": "date"})


def class_choices(year=None):
    year = year or AcademicYear.current()
    return ClassGroup.objects.filter(academic_year=year, status="active").select_related("grade") if year else ClassGroup.objects.none()


class PhoneField(forms.CharField):
    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        try:
            return normalise_phone(value)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)


class RegistrationForm(forms.Form):
    first_name = forms.CharField()
    last_name = forms.CharField()
    other_names = forms.CharField(required=False)
    date_of_birth = forms.DateField(required=False, widget=DATE)
    gender = forms.ChoiceField(choices=[("", "—"), ("F", "Female"), ("M", "Male")], required=False)
    class_group = forms.ModelChoiceField(queryset=ClassGroup.objects.none(), label="Class")
    start_date = forms.DateField(label="Enrolment date", widget=DATE)
    previous_school = forms.CharField(required=False)
    guardian_name = forms.CharField(label="Guardian full name")
    relationship = forms.CharField(required=False, help_text="e.g. Mother, Uncle")
    guardian_phone = PhoneField(label="Guardian mobile", help_text="e.g. 0971234567")
    guardian_email = forms.EmailField(required=False)
    support_category = forms.ChoiceField(
        choices=[("", "—"), ("medical", "Medical"), ("support", "Support")], required=False, label="Note type"
    )
    support_note = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Important medical or support note",
        help_text="Optional. Only authorised staff can read it. Keep it to what staff need to know.",
    )
    # Second step: filled in when the first submit found matches.
    guardian_choice = forms.CharField(required=False, widget=forms.HiddenInput)
    override_reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False,
                                      label="Reason for saving anyway")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["class_group"].queryset = class_choices()

    def clean(self):
        data = super().clean()
        if data.get("support_note") and not data.get("support_category"):
            self.add_error("support_category", "Choose whether the note is medical or support.")
        return data


class LearnerEditForm(forms.Form):
    first_name = forms.CharField()
    last_name = forms.CharField()
    other_names = forms.CharField(required=False)
    date_of_birth = forms.DateField(required=False, widget=DATE)
    gender = forms.ChoiceField(choices=[("", "—"), ("F", "Female"), ("M", "Male")], required=False)
    previous_school = forms.CharField(required=False)


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=[(s, s.capitalize()) for s in ["active", "transferred", "withdrawn", "completed"]])
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class GuardianLinkForm(forms.Form):
    full_name = forms.CharField(label="Guardian full name")
    phone = PhoneField(label="Mobile")
    email = forms.EmailField(required=False)
    relationship = forms.CharField(required=False)
    contact_role = forms.ChoiceField(choices=[("none", "No contact role"), ("primary", "Primary contact"),
                                              ("alternative", "Alternative contact")], initial="none")
    receives_results = forms.BooleanField(required=False, initial=True)
    guardian_choice = forms.CharField(required=False, widget=forms.HiddenInput)


class GuardianEditForm(forms.Form):
    full_name = forms.CharField()
    email = forms.EmailField(required=False)


class PhoneChangeForm(forms.Form):
    phone = PhoneField(label="New mobile number")
    reason = forms.CharField(required=False)


class VerifyForm(forms.Form):
    method = forms.ChoiceField(choices=[("in_person", "In person"), ("phone_call", "Phone call"), ("other", "Other")])


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class SupportNoteForm(forms.ModelForm):
    class Meta:
        model = LearnerSupportNote
        fields = ["category", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class TransferForm(forms.Form):
    to_class = forms.ModelChoiceField(queryset=ClassGroup.objects.none(), label="New class")
    effective_date = forms.DateField(widget=DATE, help_text="First day in the new class")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, year=None, exclude=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = class_choices(year)
        self.fields["to_class"].queryset = qs.exclude(pk=exclude) if exclude else qs


class EnrollForm(forms.Form):
    class_group = forms.ModelChoiceField(queryset=ClassGroup.objects.none(), label="Class")
    start_date = forms.DateField(widget=DATE)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["class_group"].queryset = class_choices()


class ImportUploadForm(forms.Form):
    file = forms.FileField(help_text="The official template (.xlsx) or a .csv with the same columns.")


class ImportConfirmForm(forms.Form):
    override_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Reason for importing likely duplicates",
        help_text="Leave empty to skip the flagged rows.",
    )


ROLE_LABELS = dict(LearnerGuardian._meta.get_field("contact_role").choices)
