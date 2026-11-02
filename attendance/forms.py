from django import forms
from django.utils import timezone

from school.models import ClassGroup, Grade

from .models import CASE_STATUSES, CONTACT_METHODS, REASON_CATEGORIES, AbsenceCase

DATE = forms.DateInput(attrs={"type": "date"})


def _label(value):
    return value.replace("_", " ").capitalize()


class EventForm(forms.Form):
    """A public holiday or an extra school day (school-wide)."""

    kind = forms.ChoiceField(choices=[("holiday", "Public holiday"), ("extra_school_day", "Extra school day (e.g. Saturday catch-up)")])
    name = forms.CharField(help_text="e.g. Heroes Day")
    start_date = forms.DateField(widget=DATE)
    end_date = forms.DateField(widget=DATE, required=False, help_text="Leave blank for a single day.")


class ClosureForm(forms.Form):
    """Close the whole school, one grade or one class (design §3.1)."""

    SCOPES = [("school", "The whole school"), ("grade", "One grade"), ("class", "One class")]

    scope = forms.ChoiceField(choices=SCOPES, widget=forms.RadioSelect, initial="grade")
    grade = forms.ModelChoiceField(queryset=Grade.objects.none(), required=False)
    class_group = forms.ModelChoiceField(queryset=ClassGroup.objects.none(), required=False, label="Class")
    start_date = forms.DateField(widget=DATE, help_text="The first day with no register, e.g. the day after the last exam.")
    end_date = forms.DateField(
        widget=DATE, required=False,
        help_text="Leave blank to close until the end of the term (grade or class only).",
    )
    name = forms.CharField(required=False, help_text="Optional, e.g. 'Form 4 finished exams'.")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["grade"].queryset = Grade.objects.filter(status="active").order_by("sequence")
        classes = ClassGroup.objects.filter(status="active").select_related("grade")
        if year is not None:
            classes = classes.filter(academic_year=year)
        self.fields["class_group"].queryset = classes

    def clean(self):
        data = super().clean()
        scope = data.get("scope")
        if scope == "grade" and not data.get("grade"):
            self.add_error("grade", "Choose the grade to close.")
        if scope == "class" and not data.get("class_group"):
            self.add_error("class_group", "Choose the class to close.")
        if scope == "school" and not data.get("end_date"):
            self.add_error("end_date", "A whole-school closure needs an end date.")
        return data


class EndEventForm(forms.Form):
    end_date = forms.DateField(widget=DATE, initial=timezone.localdate, help_text="The last day of the closure.")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ContactForm(forms.Form):
    contact_date = forms.DateField(widget=DATE, initial=timezone.localdate)
    contact_method = forms.ChoiceField(choices=[(m, _label(m)) for m in CONTACT_METHODS])
    contacted_guardian = forms.ChoiceField(required=False, label="Who was contacted")
    outcome = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), help_text="What was said or agreed.")
    reason_category = forms.ChoiceField(
        required=False, label="Reason for absence", choices=[("", "Not known yet")] + [(r, _label(r)) for r in REASON_CATEGORIES]
    )
    reason_text = forms.CharField(required=False, label="Reason details")
    next_action = forms.CharField(required=False)
    next_action_due = forms.DateField(widget=DATE, required=False, label="Next action by")

    def __init__(self, *args, guardians=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contacted_guardian"].choices = [("", "—")] + [(g.pk, g.full_name) for g in guardians]


class NoteForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Note")


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=[(s, _label(s)) for s in CASE_STATUSES if s != AbsenceCase.NEW])
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Required when resolving (how it was resolved) or referring to the Headteacher (why).",
    )
