from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.phone import normalise_phone
from school.models import SchoolClass

from .models import Enrollment, GuardianContact, Learner, LearnerGuardian, LearnerSupportNote

DATE = forms.DateInput(attrs={"type": "date"})


def class_choices(school, years=("current", "planned")):
    return (
        SchoolClass.objects.filter(school=school, status="active", academic_year__status__in=years)
        .select_related("academic_year", "grade")
        .order_by("academic_year__start_date", "grade__sequence", "stream")
    )


class ClassChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.academic_year})"


def _phone(value):
    try:
        return normalise_phone(value)
    except ValidationError as exc:
        raise ValidationError(exc.messages) from None


class LearnerForm(forms.ModelForm):
    class Meta:
        model = Learner
        fields = ["first_name", "other_names", "last_name", "gender", "date_of_birth", "previous_school"]
        widgets = {"date_of_birth": DATE}


class RegistrationForm(LearnerForm):
    """Learner, class and primary guardian in one form. An optional second guardian
    becomes the alternative contact."""

    registration_date = forms.DateField(initial=timezone.localdate, widget=DATE)
    school_class = ClassChoiceField(queryset=SchoolClass.objects.none(), label="Class")

    guardian_name = forms.CharField(label="Primary guardian's full name", max_length=200)
    guardian_phone = forms.CharField(label="Primary guardian's phone", help_text="e.g. 0971234567")
    guardian_relationship = forms.CharField(label="Relationship to learner", max_length=50, help_text="e.g. Mother")
    guardian_email = forms.EmailField(label="Primary guardian's email", required=False)
    guardian_choice = forms.CharField(required=False, widget=forms.HiddenInput)

    alt_name = forms.CharField(label="Second guardian's full name", max_length=200, required=False)
    alt_phone = forms.CharField(label="Second guardian's phone", required=False)
    alt_relationship = forms.CharField(label="Relationship to learner", max_length=50, required=False)
    alt_choice = forms.CharField(required=False, widget=forms.HiddenInput)

    override_reason = forms.CharField(
        label="Reason for saving anyway", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    class Meta(LearnerForm.Meta):
        fields = LearnerForm.Meta.fields

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_class"].queryset = class_choices(school)

    def clean_guardian_phone(self):
        return _phone(self.cleaned_data["guardian_phone"])

    def clean_alt_phone(self):
        value = self.cleaned_data.get("alt_phone")
        return _phone(value) if value else ""

    def clean(self):
        data = super().clean()
        alt = [data.get("alt_name"), data.get("alt_phone"), data.get("alt_relationship")]
        if any(alt) and not all(alt):
            raise ValidationError("For a second guardian, give their name, phone and relationship.")
        return data


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=Learner.STATUSES)
    effective_date = forms.DateField(initial=timezone.localdate, widget=DATE, help_text="The first day away from school.")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class AddGuardianForm(forms.Form):
    full_name = forms.CharField(max_length=200)
    phone = forms.CharField(help_text="e.g. 0971234567")
    email = forms.EmailField(required=False)
    relationship = forms.CharField(max_length=50)
    contact_role = forms.ChoiceField(choices=LearnerGuardian.ROLES, initial="none")
    receives_results = forms.BooleanField(required=False, initial=True)
    guardian_choice = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_phone(self):
        return _phone(self.cleaned_data["phone"])


class LinkForm(forms.ModelForm):
    class Meta:
        model = LearnerGuardian
        fields = ["relationship", "receives_results"]


class GuardianForm(forms.Form):
    full_name = forms.CharField(max_length=200)
    email = forms.EmailField(required=False)


class PhoneForm(forms.Form):
    phone = forms.CharField(label="New phone number", help_text="The old number is kept in the history.")

    def clean_phone(self):
        return _phone(self.cleaned_data["phone"])


class VerifyForm(forms.Form):
    method = forms.ChoiceField(choices=GuardianContact.METHODS, label="How was the number confirmed?")


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class TransferForm(forms.Form):
    school_class = ClassChoiceField(queryset=SchoolClass.objects.none(), label="New class")
    effective_date = forms.DateField(initial=timezone.localdate, widget=DATE, help_text="First day in the new class.")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, enrollment: Enrollment, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_class"].queryset = class_choices(enrollment.school).filter(
            academic_year=enrollment.academic_year
        ).exclude(pk=enrollment.school_class_id)


class CorrectForm(forms.Form):
    school_class = ClassChoiceField(queryset=SchoolClass.objects.none(), label="Correct class")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, enrollment: Enrollment, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_class"].queryset = class_choices(enrollment.school).filter(
            academic_year=enrollment.academic_year
        ).exclude(pk=enrollment.school_class_id)


class EnrollForm(forms.Form):
    school_class = ClassChoiceField(queryset=SchoolClass.objects.none(), label="Class")
    start_date = forms.DateField(initial=timezone.localdate, widget=DATE)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_class"].queryset = class_choices(school)


class NoteForm(forms.ModelForm):
    class Meta:
        model = LearnerSupportNote
        fields = ["category", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class ImportForm(forms.Form):
    file = forms.FileField(label="CSV file")
    dry_run = forms.BooleanField(label="Check only, don't save", required=False, initial=True)
    override_reason = forms.CharField(
        label="Save likely duplicates anyway, because…",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Leave blank unless the check flagged learners you have confirmed are new.",
    )
