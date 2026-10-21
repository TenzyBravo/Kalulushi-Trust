"""All-or-nothing pupil import from CSV.

Every row is validated before anything is written. Re-running the same file
updates existing pupils (matched by admission number) and guardians (matched
by phone) rather than creating duplicates.
"""
import csv
import datetime
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from academics.models import Enrollment
from school.models import AcademicYear

from .models import Guardian, Student, StudentGuardian
from .phone import normalise_phone

REQUIRED_COLUMNS = ["admission_number", "first_name", "last_name", "gender", "guardian_name", "guardian_phone"]
GENDERS = {"F": "F", "FEMALE": "F", "M": "M", "MALE": "M"}
OPTIONAL_COLUMNS = ["other_names", "date_of_birth", "class", "guardian_email", "relationship"]


class ImportFailed(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) found. Nothing was imported.")


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    guardians_created: int = 0
    warnings: list = field(default_factory=list)


def _parse_date(value):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError


def _validate(reader, classes):
    rows, errors, seen = [], [], {}
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        return [], [f"Missing column(s): {', '.join(missing)}"]

    for line, raw in enumerate(reader, start=2):
        row = {k: (raw.get(k) or "").strip() for k in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
        if not any(row.values()):
            continue

        def problem(msg):
            errors.append(f"Line {line}: {msg}")

        for col in REQUIRED_COLUMNS:
            if not row[col]:
                problem(f"{col} is required")

        adm = row["admission_number"]
        if adm:
            if adm in seen:
                problem(f"admission_number {adm} also appears on line {seen[adm]}")
            seen[adm] = line

        gender = GENDERS.get(row["gender"].upper())
        if row["gender"] and gender is None:
            problem("gender must be F or M")
        row["gender"] = gender

        if row["date_of_birth"]:
            try:
                row["date_of_birth"] = _parse_date(row["date_of_birth"])
            except ValueError:
                problem(f"date_of_birth '{row['date_of_birth']}' must be YYYY-MM-DD or DD/MM/YYYY")
        else:
            row["date_of_birth"] = None

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
            key = row["class"].upper().replace(" ", "")
            if classes is None:
                problem("class given but no academic year is set as current (use --year)")
            elif key not in classes:
                problem(f"class '{row['class']}' does not exist for this academic year")
            else:
                row["class"] = classes[key]
        else:
            row["class"] = None

        rows.append(row)
    return rows, errors


def _save_row(row, year, result):
    student, created = Student.objects.update_or_create(
        admission_number=row["admission_number"],
        defaults={
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "other_names": row["other_names"],
            "gender": row["gender"],
            "date_of_birth": row["date_of_birth"],
            "is_active": True,
        },
    )
    if created:
        result.created += 1
    else:
        result.updated += 1

    guardian, g_created = Guardian.objects.get_or_create(
        phone=row["guardian_phone"],
        defaults={"full_name": row["guardian_name"], "email": row["guardian_email"]},
    )
    if g_created:
        result.guardians_created += 1
    elif guardian.full_name != row["guardian_name"]:
        result.warnings.append(
            f"{row['admission_number']}: guardian {guardian.phone} is already saved as "
            f"'{guardian.full_name}', not '{row['guardian_name']}'; kept the saved name"
        )
    if row["guardian_email"] and not guardian.email:
        guardian.email = row["guardian_email"]
        guardian.save(update_fields=["email"])

    has_primary = student.guardian_links.filter(is_primary=True).exclude(guardian=guardian).exists()
    link, _ = StudentGuardian.objects.get_or_create(student=student, guardian=guardian)
    if row["relationship"]:
        link.relationship = row["relationship"]
    if not has_primary:
        link.is_primary = True
    link.save()

    if row["class"] is not None:
        Enrollment.objects.update_or_create(
            student=student, academic_year=year, defaults={"class_group": row["class"]}
        )


def import_students(fileobj, year=None, dry_run=False):
    """Import pupils from an open CSV file. Raises ImportFailed if any row is bad."""
    year = year or AcademicYear.current()
    classes = None
    if year is not None:
        classes = {
            c.name.upper(): c
            for c in year.class_groups.select_related("grade")
        }

    reader = csv.DictReader(fileobj)
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
    rows, errors = _validate(reader, classes)
    if errors:
        raise ImportFailed(errors)

    result = ImportResult()
    with transaction.atomic():
        for row in rows:
            _save_row(row, year, result)
        if dry_run:
            transaction.set_rollback(True)
    return result
