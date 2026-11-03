from django import forms

from .models import MISSING_STATUSES, AssessmentType

DATE = forms.DateInput(attrs={"type": "date"})
MISSING_CHOICES = [("", "—")] + [(m, m.replace("_", " ").capitalize()) for m in MISSING_STATUSES]


class AssessmentForm(forms.Form):
    title = forms.CharField(help_text="e.g. Test 1, Mid-term exam")
    component = forms.ChoiceField(help_text="Which part of the term score it counts towards.")
    assessment_type = forms.ModelChoiceField(queryset=AssessmentType.objects.filter(status="active"), label="Type")
    max_score = forms.DecimalField(min_value=0.01, max_digits=6, decimal_places=2, label="Out of", initial=100)
    assessed_on = forms.DateField(widget=DATE, label="Date")

    def __init__(self, *args, components=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.components = {str(c.pk): c for c in components}
        self.fields["component"].choices = [(str(c.pk), str(c)) for c in components]

    def clean_component(self):
        return self.components[self.cleaned_data["component"]]


class CorrectionForm(forms.Form):
    score = forms.DecimalField(required=False, min_value=0, max_digits=6, decimal_places=2, label="Mark")
    missing_status = forms.ChoiceField(choices=MISSING_CHOICES, required=False, label="Or missing")
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Required for a submitted assessment.")


class CompetencyCorrectionForm(forms.Form):
    level = forms.ChoiceField(required=False)
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, levels=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["level"].choices = [("", "—"), ("not_assessed", "Not assessed")] + [(str(l.pk), l.label) for l in levels]
