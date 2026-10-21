from django import forms
from django.db import transaction

from .models import Guardian, Student, StudentGuardian
from .phone import normalise_phone


class StudentForm(forms.ModelForm):
    """Add a pupil together with their primary guardian."""

    guardian_name = forms.CharField(max_length=200)
    guardian_phone = forms.CharField(max_length=20, help_text="e.g. 0971234567")
    guardian_email = forms.EmailField(required=False)
    relationship = forms.CharField(max_length=50, required=False)

    class Meta:
        model = Student
        fields = ["admission_number", "first_name", "other_names", "last_name", "gender", "date_of_birth"]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}

    def clean_guardian_phone(self):
        try:
            return normalise_phone(self.cleaned_data["guardian_phone"])
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.messages)

    @transaction.atomic
    def save(self, commit=True):
        student = super().save()
        guardian, created = Guardian.objects.get_or_create(
            phone=self.cleaned_data["guardian_phone"],
            defaults={
                "full_name": self.cleaned_data["guardian_name"],
                "email": self.cleaned_data["guardian_email"],
            },
        )
        StudentGuardian.objects.create(
            student=student,
            guardian=guardian,
            relationship=self.cleaned_data["relationship"],
            is_primary=True,
        )
        return student
