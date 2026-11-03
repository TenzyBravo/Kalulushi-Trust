"""Upload a year's school calendar from the template: terms, holidays, no-learner days,
no-alert days, extra school days and events.

Every row is checked first and shown in a preview. Confirming replaces the
year's previously uploaded (and auto-added) items, which are kept as
superseded for history; items added by hand stay. Terms are created or
moved to the uploaded dates.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core import audit
from school.models import ClassGroup, Grade, Term

from .calendar import SchoolCalendar
from .models import CalendarEvent

COLUMNS = ["Type", "Name", "Start date", "End date", "Applies to", "Notes"]
TYPES = {
    "term": "term",
    "public holiday": "holiday",
    "holiday": "holiday",
    "no learners": "closure",
    "closure": "closure",
    "no absence alerts": "no_alert_day",
    "extra school day": "extra_school_day",
    "event": "event",
}
TYPE_LABELS = {"term": "Term", "holiday": "Public holiday", "closure": "No learners", "no_alert_day": "No absence alerts",
               "extra_school_day": "Extra school day", "event": "Event"}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%a %d %b %Y", "%A %d %B %Y"]
GUIDE = [
    ("Term", "Term 1, Term 2 or Term 3 with its first and last day. Creates the term, or moves it to these dates."),
    ("Public holiday", "No school. Weekends are already not school days."),
    ("No learners", "Learners stay home, e.g. staff development. Leave 'Applies to' blank for the whole school, "
                    "or give a grade (Form 4) or class (5A) to close only that one."),
    ("No absence alerts", "Learners come and registers are taken, but absences never start an absence case "
                          "(e.g. parent-teacher conferences, report-card days)."),
    ("Extra school day", "A day that would not normally be a school day, e.g. a Saturday catch-up."),
    ("Event", "Shown on the calendar only (exams, sports, assemblies). Does not change registers."),
]


class CalendarFileError(Exception):
    pass


def _read(fileobj, filename):
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = fileobj.read()
        text = text.decode("utf-8-sig") if isinstance(text, bytes) else text
        return [list(r) for r in csv.reader(io.StringIO(text))]
    try:
        from openpyxl import load_workbook

        book = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception as exc:
        raise CalendarFileError("This is not the calendar template. Download the template, fill it in and upload it as .xlsx or .csv.") from exc
    sheet = book["Calendar"] if "Calendar" in book.sheetnames else book.worksheets[0]
    return [list(r) for r in sheet.iter_rows(values_only=True)]


def _date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(text)


@dataclass
class Row:
    line: int
    kind: str = ""
    name: str = ""
    start: date | None = None
    end: date | None = None
    grade: object = None
    class_group: object = None
    notes: str = ""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def label(self):
        return TYPE_LABELS.get(self.kind, self.kind)

    @property
    def scope(self):
        if self.class_group:
            return f"Class {self.class_group.name}"
        return self.grade.name if self.grade else "Whole school"

    def as_session(self):
        return {"line": self.line, "kind": self.kind, "name": self.name, "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None, "grade": self.grade.pk if self.grade else None,
                "class_group": self.class_group.pk if self.class_group else None, "notes": self.notes}


@dataclass
class Preview:
    year: object
    rows: list
    term_changes: list = field(default_factory=list)  # (number, old (start, end) or None, new (start, end))
    replaced: int = 0

    @property
    def errors(self):
        return [r for r in self.rows if r.errors]

    @property
    def warnings(self):
        return [r for r in self.rows if r.warnings]

    def counts(self):
        out = {}
        for r in self.rows:
            if r.kind != "term":
                out[r.label] = out.get(r.label, 0) + 1
        return out


def _scope(text, year):
    text = (text or "").strip()
    if not text or text.lower() in ("whole school", "all", "school"):
        return None, None
    key = text.lower().replace("class ", "").replace(" ", "")
    for g in Grade.objects.filter(school=year.school):
        if key in (g.name.lower().replace(" ", ""), g.code.lower()):
            return g, None
    c = ClassGroup.objects.filter(academic_year=year).select_related("grade")
    for cls in c:
        if key == cls.name.lower():
            return None, cls
    raise ValueError(text)


def parse(fileobj, filename, year):
    """Read and check the file against the academic year. Raises CalendarFileError if it is not the template."""
    table = _read(fileobj, filename)
    if not table:
        raise CalendarFileError("The file is empty.")
    header = [str(c or "").strip().lower() for c in table[0]]
    missing = [c for c in COLUMNS[:4] if c.lower() not in header]
    if missing:
        raise CalendarFileError(f"Missing column(s): {', '.join(missing)}. Use the calendar template.")
    index = {c: header.index(c.lower()) if c.lower() in header else None for c in COLUMNS}

    def cell(raw, column):
        i = index[column]
        return raw[i] if i is not None and i < len(raw) else None

    rows = []
    for line, raw in enumerate(table[1:], start=2):
        if not any(v not in (None, "") for v in raw):
            continue
        row = Row(line=line, name=str(cell(raw, "Name") or "").strip(), notes=str(cell(raw, "Notes") or "").strip())
        kind_text = str(cell(raw, "Type") or "").strip().lower()
        row.kind = TYPES.get(kind_text, "")
        if not row.kind:
            row.errors.append(f"unknown type '{cell(raw, 'Type') or ''}' (use {', '.join(TYPE_LABELS.values())})")
        if not row.name:
            row.errors.append("give a name")
        try:
            row.start = _date(cell(raw, "Start date"))
            row.end = _date(cell(raw, "End date")) or row.start
        except ValueError as exc:
            row.errors.append(f"'{exc}' is not a date (use YYYY-MM-DD or DD/MM/YYYY)")
        if row.start is None and not any("date" in e for e in row.errors):
            row.errors.append("give a start date")
        if row.start and row.end:
            if row.end < row.start:
                row.errors.append("ends before it starts")
            if not (year.contains(row.start) and year.contains(row.end)):
                row.errors.append(f"not inside academic year {year} ({year.start_date} to {year.end_date})")
        applies = cell(raw, "Applies to")
        if applies not in (None, ""):
            if row.kind != "closure":
                row.errors.append("only 'No learners' rows can apply to one grade or class")
            else:
                try:
                    row.grade, row.class_group = _scope(applies, year)
                except ValueError:
                    row.errors.append(f"'{applies}' is not a grade or a {year} class")
        rows.append(row)
    return check(Preview(year=year, rows=rows))


def check(preview):
    """Cross-row checks: terms, and what each row actually changes."""
    year = preview.year
    terms = [r for r in preview.rows if r.kind == "term" and not r.errors]
    numbers = {}
    for r in terms:
        digits = "".join(ch for ch in r.name if ch.isdigit())
        if digits not in ("1", "2", "3"):
            r.errors.append("name terms Term 1, Term 2 or Term 3")
            continue
        if digits in numbers:
            r.errors.append(f"Term {digits} appears twice (also line {numbers[digits].line})")
        numbers[digits] = r
    ordered = sorted((r for r in numbers.values() if not r.errors), key=lambda r: r.start)
    for a, b in zip(ordered, ordered[1:]):
        if b.start <= a.end:
            b.errors.append(f"overlaps {a.name}")
    existing = {t.number: t for t in Term.objects.filter(academic_year=year)}
    for r in ordered:  # an uploaded term must not overlap a term the file leaves as it is
        for n, t in existing.items():
            if str(n) not in numbers and r.start <= t.end_date and t.start_date <= r.end:
                r.errors.append(f"overlaps the existing Term {n} ({t.start_date} to {t.end_date})")
    for digits, r in sorted(numbers.items()):
        old = existing.get(int(digits))
        if old is None or (old.start_date, old.end_date) != (r.start, r.end):
            preview.term_changes.append((int(digits), (old.start_date, old.end_date) if old else None, (r.start, r.end)))

    # Judge effects against the terms as they will be after the upload.
    planned = {int(d): (r.start, r.end) for d, r in numbers.items() if not r.errors}
    spans = [planned.get(n, (t.start_date, t.end_date)) for n, t in existing.items()] + \
            [span for n, span in planned.items() if n not in existing]

    def in_term(day):
        return any(s <= day <= e for s, e in spans)

    for r in preview.rows:
        if r.errors or r.kind in ("term", "event", "extra_school_day") or not r.start:
            continue
        days = [r.start + timedelta(days=i) for i in range((r.end - r.start).days + 1)]
        affected = [d for d in days if d.weekday() < 5 and in_term(d)]
        if not affected:
            r.warnings.append("falls on weekends or outside term time, so it does not change any register")
    preview.replaced = CalendarEvent.objects.filter(academic_year=year, source__in=["upload", "seed"],
                                                    superseded_at__isnull=True).count()
    return preview


def from_session(data, year):
    rows = []
    grades = {g.pk: g for g in Grade.objects.filter(school=year.school)}
    classes = {c.pk: c for c in ClassGroup.objects.filter(academic_year=year)}
    for d in data:
        rows.append(Row(line=d["line"], kind=d["kind"], name=d["name"], notes=d["notes"],
                        start=date.fromisoformat(d["start"]) if d["start"] else None,
                        end=date.fromisoformat(d["end"]) if d["end"] else None,
                        grade=grades.get(d["grade"]), class_group=classes.get(d["class_group"])))
    return check(Preview(year=year, rows=rows))


@transaction.atomic
def apply(preview, actor):
    if not actor.has_perm("attendance.add_calendarevent"):
        raise PermissionDenied
    if preview.errors:
        raise ValidationError("Fix the problems in the file first.")
    year = preview.year
    now = timezone.now()

    # Terms: park the ones that move on placeholder dates first, so the no-overlap rule never trips mid-way.
    existing = {t.number: t for t in Term.objects.select_for_update().filter(academic_year=year)}
    for number, _old, _new in preview.term_changes:
        if number in existing:
            t = existing[number]
            t.start_date, t.end_date = date(1900 + number, 1, 1), date(1900 + number, 1, 2)
            t.save(update_fields=["start_date", "end_date"])
    for number, old, (start, end) in preview.term_changes:
        term = existing.get(number) or Term(academic_year=year, number=number)
        term.start_date, term.end_date = start, end
        term.save()
        audit.record("term.upload", term, actor=actor, school=year.school,
                     changes={"dates": [f"{old[0]} to {old[1]}" if old else None, f"{start} to {end}"]})

    replaced = CalendarEvent.objects.filter(academic_year=year, source__in=["upload", "seed"], superseded_at__isnull=True)
    replaced_count = replaced.update(superseded_at=now)
    created = 0
    for r in preview.rows:
        if r.kind == "term":
            continue
        event = CalendarEvent(
            school=year.school, academic_year=year, kind=r.kind, name=r.name, start_date=r.start, end_date=r.end,
            grade=r.grade, class_group=r.class_group, reason=r.notes or (r.name if r.kind == "closure" else ""),
            source="upload", created_by=actor,
        )
        event.full_clean()
        event.save()
        created += 1
    audit.record("calendar.upload", year, actor=actor, school=year.school,
                 changes={"added": created, "replaced": replaced_count, "terms": [n for n, _, _ in preview.term_changes],
                          **{label: n for label, n in preview.counts().items()}})
    return created, replaced_count


def workbook(rows=()):
    """The template as an .xlsx file (bytes), optionally pre-filled with [(type label, name, start, end, applies to, notes)]."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    book = Workbook()
    sheet = book.active
    sheet.title = "Calendar"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F2F4C")
    for row in rows:
        sheet.append(list(row))
    for column, width in zip("ABCDEF", (20, 38, 14, 14, 16, 60)):
        sheet.column_dimensions[column].width = width
    for r in range(2, sheet.max_row + 1):
        for c in ("C", "D"):
            sheet[f"{c}{r}"].number_format = "yyyy-mm-dd"
    choice = DataValidation(type="list", formula1='"' + ",".join(TYPE_LABELS.values()) + '"', allow_blank=True)
    sheet.add_data_validation(choice)
    choice.add(f"A2:A{max(sheet.max_row, 300)}")
    sheet.freeze_panes = "A2"

    guide = book.create_sheet("How to fill")
    guide.append(["Type", "What it does"])
    for cell in guide[1]:
        cell.font = Font(bold=True)
    for item in GUIDE:
        guide.append(list(item))
    guide.append([])
    guide.append(["Dates", "YYYY-MM-DD (2026-01-12) or DD/MM/YYYY. Leave 'End date' blank for a single day."])
    guide.append(["Uploading again", "Replaces the earlier upload for that year; items added by hand on the Calendar screen stay."])
    guide.column_dimensions["A"].width = 20
    guide.column_dimensions["B"].width = 110
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
