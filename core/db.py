"""Small database helpers shared by every app."""
from django.contrib.postgres.fields import DateRangeField
from django.db import models


class CITextField(models.TextField):
    """PostgreSQL citext: compared case-insensitively, stored as typed."""

    def db_type(self, connection):
        return "citext"


class DateRange(models.Func):
    """daterange(start, end, '[]'): an inclusive range; a null end is open-ended."""

    function = "daterange"
    output_field = DateRangeField()


def status_check(name, field, values):
    return models.CheckConstraint(condition=models.Q(**{f"{field}__in": values}), name=name)


def choices(*values):
    return [(v, v.replace("_", " ").capitalize()) for v in values]
