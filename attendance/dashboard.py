"""'Waiting on you' items for the home dashboard (design §7)."""
from django.urls import reverse
from django.utils import timezone

from school.models import AcademicYear
from staffing.access import sees_whole_school

from . import access, services
from .calendar import for_days
from .models import AbsenceCase, AttendanceSession


def _not_taken(classes, day, school):
    cal = for_days(day, day, school)
    taken = set(AttendanceSession.objects.filter(class_group__in=classes, date=day).values_list("class_group_id", flat=True))
    return [c for c in classes if c.pk not in taken and not services.register_state(c, day, cal)]


def attention_items(user, day=None):
    """[(count, label, link)] for registers and absence cases."""
    day = day or timezone.localdate()
    items = []
    if not user.has_perm("attendance.view_attendancesession"):
        return items
    today_url = reverse("attendance_today")
    followup_url = reverse("attendance_followup")
    if sees_whole_school(user):
        year = AcademicYear.current(user.school)
        classes = list(year.classes.filter(status="active")) if year else []
        items.append((len(_not_taken(classes, day, user.school)), "registers not taken today", today_url))
        cases = access.visible_cases(user)
        items.append((cases.filter(status=AbsenceCase.NEW).count(), "new absence cases", followup_url))
        if access.can_review_escalations(user):
            items.append((cases.filter(status=AbsenceCase.ESCALATED).count(), "escalated absence cases",
                          f"{followup_url}?status=escalated"))
    else:
        mine = list(access.register_classes(user, day))
        for c in _not_taken(mine, day, user.school):
            items.append((1, f"register for {c.name} not taken today", f"{reverse('attendance_register', args=[c.pk])}?date={day.isoformat()}"))
        open_cases = access.visible_cases(user).exclude(status=AbsenceCase.RESOLVED).count()
        items.append((open_cases, "open absence cases in your class", followup_url))
    return items
