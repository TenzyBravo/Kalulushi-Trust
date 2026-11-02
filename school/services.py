from django.core.exceptions import ValidationError
from django.db import transaction

from audit import log

from .models import AcademicYear


@transaction.atomic
def make_year_current(year, actor=None):
    """Close the current year and open this one, in one transaction (design §4)."""
    year = AcademicYear.objects.select_for_update().get(pk=year.pk)
    if year.status == "current":
        return year
    if year.status == "closed":
        raise ValidationError(f"{year} is closed and cannot be made current again.")
    previous = AcademicYear.objects.select_for_update().filter(school=year.school, status="current").first()
    if previous:
        previous.status = "closed"
        previous.save(update_fields=["status"])
        log.record("academic_year.close", previous, changes={"status": ["current", "closed"]}, actor=actor)
    year.status = "current"
    year.save(update_fields=["status"])
    log.record("academic_year.make_current", year, changes={"status": ["planned", "current"]}, actor=actor)
    return year
