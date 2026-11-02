"""Learner numbers: {school code}-{6-digit sequence}{Luhn check digit}, e.g. KLT-0001234 (decision D4)."""
import re

from django.db import connection

from .models import LearnerNumberSequence

SEQUENCE_DIGITS = 6
_NUMBER = re.compile(r"^(?P<code>[A-Z0-9]+)-(?P<digits>\d{%d})$" % (SEQUENCE_DIGITS + 1))


def luhn_digit(digits):
    """The check digit that makes digits + check pass the Luhn (mod 10) test."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # positions that get doubled once the check digit is appended
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def format_number(school_code, value):
    digits = f"{value:0{SEQUENCE_DIGITS}d}"
    if len(digits) > SEQUENCE_DIGITS:
        raise OverflowError(f"Learner sequence for {school_code} has run past {SEQUENCE_DIGITS} digits.")
    return f"{school_code}-{digits}{luhn_digit(digits)}"


def is_valid(number):
    """True if number is well formed and its check digit matches, i.e. probably typed correctly."""
    match = _NUMBER.match((number or "").strip().upper())
    if not match:
        return False
    digits = match.group("digits")
    return luhn_digit(digits[:-1]) == digits[-1]


def next_number(school):
    """Take the next number. Call inside the registration transaction: the row lock means two
    simultaneous registrations cannot get the same number, and a rollback leaves a harmless gap."""
    assert connection.in_atomic_block, "next_number() must run inside the registration transaction"
    sequence, _ = LearnerNumberSequence.objects.get_or_create(school=school)
    sequence = LearnerNumberSequence.objects.select_for_update().get(pk=sequence.pk)
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    return format_number(school.code, sequence.last_value)
