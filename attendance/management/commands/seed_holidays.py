"""Add Zambia's public holidays for a year to the school calendar. Safe to re-run.

Dates follow the Public Holidays Act as usually observed; check them against the
government's announcements each year and adjust in the Calendar screen.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts import roles
from accounts.models import User
from attendance.models import CalendarEvent
from core import audit
from core.models import School
from school.models import AcademicYear


def easter_sunday(year):
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def first_monday(year, month):
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def zambian_holidays(year):
    easter = easter_sunday(year)
    heroes = first_monday(year, 7)
    holidays = [
        ("New Year's Day", date(year, 1, 1)),
        ("International Women's Day", date(year, 3, 8)),
        ("Youth Day", date(year, 3, 12)),
        ("Good Friday", easter - timedelta(days=2)),
        ("Holy Saturday", easter - timedelta(days=1)),
        ("Easter Monday", easter + timedelta(days=1)),
        ("Kenneth Kaunda Day", date(year, 4, 28)),
        ("Labour Day", date(year, 5, 1)),
        ("Africa Freedom Day", date(year, 5, 25)),
        ("Heroes Day", heroes),
        ("Unity Day", heroes + timedelta(days=1)),
        ("Farmers' Day", first_monday(year, 8)),
        ("National Day of Prayer", date(year, 10, 18)),
        ("Independence Day", date(year, 10, 24)),
        ("Christmas Day", date(year, 12, 25)),
    ]
    observed = []
    for name, day in holidays:
        observed.append((name, day))
        if day.weekday() == 6:  # a holiday on a Sunday is observed on the Monday
            observed.append((f"{name} (observed)", day + timedelta(days=1)))
    return observed


class Command(BaseCommand):
    help = (
        "Add Zambia's public holidays to the school calendar. With no --year, covers the current and any "
        "planned academic years. Safe to re-run: only missing holidays are added. Runs on every deploy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--year", help="Academic year name, e.g. 2027 (default: current and planned years)")
        parser.add_argument("--as-user", help="Username recorded as adding them (default: the first System Administrator)")

    def _actor(self, as_user):
        if as_user:
            try:
                return User.objects.get(username=as_user, is_active=True)
            except User.DoesNotExist:
                raise CommandError(f"No active user '{as_user}'.")
        return (
            User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
            or User.objects.filter(is_active=True, groups__name=roles.SYSTEM_ADMIN).order_by("pk").first()
        )

    @transaction.atomic
    def handle(self, year=None, as_user=None, **options):
        school = School.current()
        if year:
            years = list(AcademicYear.objects.filter(school=school, name=year))
            if not years:
                raise CommandError(f"No academic year '{year}'.")
        else:
            years = list(AcademicYear.objects.filter(school=school, status__in=[AcademicYear.CURRENT, AcademicYear.PLANNED]))
        actor = self._actor(as_user)
        if not years or actor is None:
            self.stdout.write("No academic year or administrator yet; public holidays not added.")
            return
        for academic_year in years:
            added = 0
            for name, day in zambian_holidays(academic_year.start_date.year):
                if not academic_year.contains(day):
                    continue
                _, created = CalendarEvent.objects.get_or_create(
                    school=school, academic_year=academic_year, kind=CalendarEvent.HOLIDAY, name=name, start_date=day,
                    defaults={"end_date": day, "created_by": actor},
                )
                added += created
            if added:
                audit.record("calendar_event.seed_holidays", academic_year, school=school, actor=actor,
                             changes={"added": added})
            self.stdout.write(self.style.SUCCESS(f"Added {added} public holiday(s) to {academic_year}."))
