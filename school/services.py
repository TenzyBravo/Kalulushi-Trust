from django.db import transaction

from core import audit

from .models import AcademicYear


@transaction.atomic
def make_year_current(year, actor):
    """Close the current year and open this one, in one audited transaction (design §4)."""
    year = AcademicYear.objects.select_for_update().get(pk=year.pk)
    previous = AcademicYear.objects.select_for_update().filter(school=year.school, status=AcademicYear.CURRENT).exclude(pk=year.pk).first()
    if previous:
        previous.status = AcademicYear.CLOSED
        previous.save(update_fields=["status"])
        audit.record("academic_year.close", previous, changes={"status": ["current", "closed"]}, actor=actor)
    old = year.status
    year.status = AcademicYear.CURRENT
    year.save(update_fields=["status"])
    audit.record("academic_year.make_current", year, changes={"status": [old, "current"]}, actor=actor)
    return year
