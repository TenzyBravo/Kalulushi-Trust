"""School days and register days (design §3).

A school day falls inside a term and is either a weekday not covered by a
holiday or school-wide closure, or an extra school day. A register day for a
class is a school day not covered by a closure for that class or its grade.
"""
from datetime import timedelta

from django.db.models import Q

from core.models import School
from school.models import Term

from .models import CalendarEvent


class SchoolCalendar:
    """Terms and calendar events for a date range, loaded once."""

    def __init__(self, school, start, end):
        self.school = school
        # Whole academic years, so an open-ended closure that began earlier still finds its term.
        self.terms = list(
            Term.objects.filter(
                academic_year__school=school, academic_year__start_date__lte=end, academic_year__end_date__gte=start
            ).select_related("academic_year").order_by("start_date")
        )
        lo = min([start] + [t.start_date for t in self.terms])
        self.events = list(
            CalendarEvent.objects.filter(school=school, start_date__lte=end)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=lo))
            .select_related("grade", "class_group")
        )

    def term_for(self, day):
        return next((t for t in self.terms if t.start_date <= day <= t.end_date), None)

    def _event_end(self, event):
        if event.end_date:
            return event.end_date
        term = self.term_for(event.start_date)  # open-ended closures last to the end of their term
        return term.end_date if term else event.start_date

    def _covering(self, day, kind):
        return [e for e in self.events if e.kind == kind and e.start_date <= day <= self._event_end(e)]

    def is_school_day(self, day):
        if self.term_for(day) is None:
            return False
        if self._covering(day, CalendarEvent.EXTRA_SCHOOL_DAY):
            return True
        if day.weekday() >= 5:
            return False
        if self._covering(day, CalendarEvent.HOLIDAY):
            return False
        return not any(e for e in self._covering(day, CalendarEvent.CLOSURE) if not e.grade_id and not e.class_group_id)

    def non_school_reason(self, day):
        """Why a day is not a school day, for the register screen."""
        if self.term_for(day) is None:
            return "This date is outside term time."
        if day.weekday() >= 5 and not self._covering(day, CalendarEvent.EXTRA_SCHOOL_DAY):
            return "This date is a weekend."
        for kind in (CalendarEvent.HOLIDAY, CalendarEvent.CLOSURE):
            for e in self._covering(day, kind):
                if not e.grade_id and not e.class_group_id:
                    return f"{e.name}: the school is closed." if kind == CalendarEvent.CLOSURE else f"{e.name} (public holiday)."
        return ""

    def closure_for(self, class_group, day):
        """A grade or class closure covering this class on this day, if any (§3.1)."""
        for e in self._covering(day, CalendarEvent.CLOSURE):
            if e.class_group_id == class_group.pk or (e.grade_id and e.grade_id == class_group.grade_id):
                return e
        return None

    def is_register_day(self, day, class_group):
        return self.is_school_day(day) and self.closure_for(class_group, day) is None

    def school_days_between(self, after, until):
        """School days d with after < d <= until."""
        days, d = [], after + timedelta(days=1)
        while d <= until:
            if self.is_school_day(d):
                days.append(d)
            d += timedelta(days=1)
        return days


def for_days(start, end, school=None):
    return SchoolCalendar(school or School.current(), start, end)


def is_school_day(day, school=None):
    return for_days(day, day, school).is_school_day(day)


def is_register_day(day, class_group):
    return for_days(day, day, class_group.school).is_register_day(day, class_group)
