"""Zambian mobile numbers, stored as +260XXXXXXXXX.

Accepts the forms people actually write: 0971234567, 971234567, 260971234567,
+260 97 123 4567, 097-123-4567.
"""
import re

from django.core.exceptions import ValidationError

COUNTRY_CODE = "260"
# National significant number: 9 digits, mobile ranges start with 7 or 9
# (Airtel 97/77/57, MTN 96/76/56, Zamtel 95/75/55 and so on).
_MOBILE_NSN = re.compile(r"^[579]\d{8}$")


def normalise_phone(value):
    """Return the +260 form of a Zambian mobile number, or raise ValidationError."""
    raw = (value or "").strip()
    digits = re.sub(r"[\s\-().]", "", raw)
    if digits.startswith("+"):
        digits = digits[1:]
        if not digits.startswith(COUNTRY_CODE):
            raise ValidationError(f"'{raw}' is not a Zambian number (+260).")
    if digits.startswith("00" + COUNTRY_CODE):
        digits = digits[2:]
    if digits.startswith(COUNTRY_CODE) and len(digits) == 12:
        nsn = digits[3:]
    elif digits.startswith("0") and len(digits) == 10:
        nsn = digits[1:]
    else:
        nsn = digits
    if not nsn.isdigit() or not _MOBILE_NSN.match(nsn):
        raise ValidationError(f"'{raw}' is not a valid Zambian mobile number.")
    return f"+{COUNTRY_CODE}{nsn}"


def validate_phone(value):
    normalise_phone(value)
