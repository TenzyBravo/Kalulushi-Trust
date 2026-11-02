"""Learner numbers: {school code}-{6-digit sequence}{Luhn check digit}, e.g. KLT-0001234 (D4)."""
import re

from .models import LearnerNumberSequence

_PATTERN = re.compile(r"^([A-Z0-9]+)-(\d{6})(\d)$")


def luhn_digit(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def format_number(code, value):
    body = f"{value:06d}"
    return f"{code}-{body}{luhn_digit(body)}"


def is_valid(number):
    match = _PATTERN.match((number or "").strip().upper())
    return bool(match) and luhn_digit(match.group(2)) == match.group(3)


def looks_like_number(text):
    return bool(re.match(r"^[A-Za-z]+-\d+$", (text or "").strip()))


def next_number(school):
    """Must run inside the registration transaction: the row lock stops two registrations sharing a number."""
    LearnerNumberSequence.objects.get_or_create(school=school)
    seq = LearnerNumberSequence.objects.select_for_update().get(school=school)
    seq.last_value += 1
    seq.save(update_fields=["last_value"])
    return format_number(school.code, seq.last_value)
