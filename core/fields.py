"""Model fields and expressions shared by every app."""
from django.contrib.postgres.fields import DateRangeField
from django.db import models
from django.db.models import Func, Q, Value


class CICharField(models.CharField):
    """Stored as citext, so lookups and uniqueness ignore case."""

    def db_type(self, connection):
        return "citext"


class CIEmailField(models.EmailField):
    """Stored as citext, so lookups and uniqueness ignore case."""

    def db_type(self, connection):
        return "citext"


class DateRange(Func):
    function = "daterange"
    output_field = DateRangeField()


def period(start="start_date", end="end_date"):
    """daterange(start, end, '[]'): both ends inclusive, a null end is open-ended."""
    return DateRange(start, end, Value("[]"))


def overlapping(start, end, start_field="start_date", end_field="end_date"):
    """Q matching rows whose inclusive period overlaps [start, end]. A None end is open."""
    q = Q(**{f"{end_field}__isnull": True}) | Q(**{f"{end_field}__gte": start})
    if end is not None:
        q &= Q(**{f"{start_field}__lte": end})
    return q


def status_check(name, choices, field="status"):
    return models.CheckConstraint(
        condition=Q(**{f"{field}__in": [value for value, _ in choices]}), name=name
    )
