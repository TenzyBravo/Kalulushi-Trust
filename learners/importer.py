"""All-or-nothing learner import from CSV.

Every row is checked first. Rows are then registered one by one through
services.register inside a single transaction, so each learner gets a number,
an enrolment, guardian links and audit records exactly as if registered on
screen. If any row fails, including a likely duplicate, nothing is saved and
every problem is listed by line number.
"""
import csv
import datetime
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction

from core.phone import normalise_phone
from school.models import AcademicYear

from . import services

REQUIRED_COLUMNS = ["first_name", "last_name", "gender", "class", "guardian_name", "guardian_phone"]
OPTIONAL_COLUMNS = [
    "other_names", "date_of_birth", "registration_date", "previous_school", "guardian_email", "relationship",
]
GENDERS = {"F": "F", "FEMALE": "F", "M": "M", "MALE": "M"}


class ImportFailed(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) found. Nothing was imported.")


@dataclass
class ImportResult:
    learners: list = field(default_factory=list)
    guardians_reused: int = 0
    warnings: list = field(default_factory=list)


def _messages(exc):
    if isinstance(exc, ValidationError):
        return exc.messages
    return [str(exc) or "not allowed"]


def _parse_date(value):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError


def _check_rows(reader, classes):
    rows, errors = [], []
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        return [], [f"Missing column(s): {', '.join(missing)}"]

    for line, raw in enumerate(reader, start=2):
        row = {k: (raw.get(k) or "").strip() for k in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
        if not any(row.values()):
            continue
        row["line"] = line

        def problem(msg):
            errors.append(f"Line {line}: {msg}")

        for col in REQUIRED_COLUMNS:
            if not row[col]:
                problem(f"{col} is required")

        gender = GENDERS.get(row["gender"].upper())
        if row["gender"] and gender is None:
            problem("gender must be F or M")
        row["gender"] = gender

        for col in ("date_of_birth", "registration_date"):
            if row[col]:
                try:
                    row[col] = _parse_date(row[col])
                except ValueError:
                    problem(f"{col} '{row[col]}' must be YYYY-MM-DD or DD/MM/YYYY")
            else:
                row[col] = None

        if row["guardian_phone"]:
            try:
                row["guardian_phone"] = normalise_phone(row["guardian_phone"])
            except ValidationError:
                problem(f"guardian_phone '{row['guardian_phone']}' is not a valid Zambian mobile number")

        if row["guardian_email"]:
            try:
                validate_email(row["guardian_email"])
            except ValidationError:
                problem(f"guardian_email '{row['guardian_email']}' is not a valid email address")

        if row["class"]:
            school_class = classes.get(row["class"].upper().replace(" ", ""))
            if school_class is None:
                problem(f"class '{row['class']}' does not exist in this academic year")
            row["class"] = school_class

        rows.append(row)
    return rows, errors


def _guardian_input(row, school, result):
    """Reuse a guardian only when both the phone and the name match; a shared handset
    with a different name becomes a new guardian (decision D3)."""
    name = row["guardian_name"]
    existing = None
    for guardian in services.guardians_with_phone(school, row["guardian_phone"]):
        if guardian.full_name.casefold() == name.casefold():
            existing = guardian
            break
        result.warnings.append(
            f"Line {row['line']}: {row['guardian_phone']} also belongs to {guardian.full_name}; "
            f"saved {name} as a separate guardian"
        )
    if existing:
        result.guardians_reused += 1
    return services.GuardianInput(
        relationship=row["relationship"] or "Guardian",
        contact_role="primary",
        existing=existing,
        full_name=name,
        phone=row["guardian_phone"],
        email=row["guardian_email"],
    )


def import_learners(actor, fileobj, *, year=None, dry_run=False, override_reason=""):
    """Import learners from an open CSV file into classes of year (default: the current year).

    override_reason saves rows flagged as likely duplicates anyway; the actor
    needs the override_duplicate permission and every override is audited.
    """
    year = year or AcademicYear.current_for(actor.school)
    if year is None:
        raise ImportFailed(["No academic year is current. Make one current first, or choose the year."])
    classes = {c.name.upper(): c for c in year.classes.filter(status="active").select_related("grade", "school", "academic_year")}

    reader = csv.DictReader(fileobj)
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
    rows, errors = _check_rows(reader, classes)
    if errors:
        raise ImportFailed(errors)
    if not rows:
        raise ImportFailed(["The file has no learners in it."])

    result = ImportResult()
    with transaction.atomic():
        for row in rows:
            try:
                learner = services.register(
                    actor,
                    school_class=row["class"],
                    guardians=[_guardian_input(row, actor.school, result)],
                    override_reason=override_reason,
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    other_names=row["other_names"],
                    gender=row["gender"],
                    date_of_birth=row["date_of_birth"],
                    previous_school=row["previous_school"],
                    **({"registration_date": row["registration_date"]} if row["registration_date"] else {}),
                )
                result.learners.append(learner)
            except services.LikelyDuplicate as dup:
                for match, why in dup.matches:
                    errors.append(f"Line {row['line']}: looks like {match.full_name} ({match.learner_number}) – {why}")
            except (ValidationError, PermissionDenied) as exc:
                errors.extend(f"Line {row['line']}: {m}" for m in _messages(exc))
        if errors or dry_run:
            transaction.set_rollback(True)
    if errors:
        raise ImportFailed(errors)
    return result
