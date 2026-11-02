from django import forms

from accounts import roles
from accounts.models import User


from .models import DUTIES, TeacherAssignment


def teaching_staff():
    return User.objects.filter(is_active=True, groups__name__in=[roles.TEACHER, roles.HEADTEACHER, roles.ACADEMIC_ADMIN]).distinct()


class AssignTeacherForm(forms.Form):
    teacher = forms.ModelChoiceField(queryset=User.objects.none())
    assignment_type = forms.ChoiceField(choices=[("primary", "Primary"), ("backup", "Backup (named for cover only)")])
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = teaching_staff()


class ClassTeacherForm(forms.Form):
    teacher = forms.ModelChoiceField(queryset=User.objects.none())
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = teaching_staff()


class CoverForm(forms.Form):
    cover_teacher = forms.ModelChoiceField(queryset=User.objects.none(), help_text="Must be a backup for this class subject.")
    starts_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    ends_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    duties = forms.MultipleChoiceField(choices=[(d, d.replace("_", " ").capitalize()) for d in DUTIES],
                                       widget=forms.CheckboxSelectMultiple)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, primary=None, **kwargs):
        super().__init__(*args, **kwargs)
        backups = TeacherAssignment.objects.filter(
            class_subject=primary.class_subject, assignment_type="backup", status="active"
        ).values_list("teacher_id", flat=True) if primary else []
        self.fields["cover_teacher"].queryset = User.objects.filter(pk__in=list(backups), is_active=True)


