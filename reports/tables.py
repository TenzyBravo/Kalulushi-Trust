"""A report is a list of tables. The same tables render on screen, in the print view and as Excel."""
import io
from dataclasses import dataclass, field

from django.db import transaction

from core import audit

from .models import ReportExport


@dataclass
class Cell:
    text: object
    url: str = ""
    tone: str = ""  # "", "good", "warn", "bad"

    def __str__(self):
        return "" if self.text is None else str(self.text)


@dataclass
class Table:
    title: str
    columns: list
    rows: list = field(default_factory=list)  # each row: list of values or Cells
    note: str = ""
    total: list | None = None  # optional totals row

    def cells(self):
        for row in self.rows:
            yield [c if isinstance(c, Cell) else Cell(c) for c in row]

    def total_cells(self):
        return [c if isinstance(c, Cell) else Cell(c) for c in self.total] if self.total else None


def pct(part, whole):
    return round(100 * part / whole) if whole else None


def pct_text(part, whole):
    value = pct(part, whole)
    return f"{value}%" if value is not None else "—"


def workbook(title, filters_text, tables):
    """All tables of a report as one .xlsx (one sheet per table), with the filters at the top."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    book = Workbook()
    book.remove(book.active)
    used = set()
    for table in tables:
        name = "".join(ch for ch in table.title if ch not in "[]:*?/\\")[:28] or "Report"
        base, n = name, 2
        while name in used:
            name, n = f"{base[:25]} {n}", n + 1
        used.add(name)
        sheet = book.create_sheet(name)
        sheet.append([title])
        sheet["A1"].font = Font(bold=True, size=13)
        sheet.append([filters_text])
        if table.note:
            sheet.append([table.note])
        sheet.append([])
        sheet.append(table.columns)
        header_row = sheet.max_row
        for cell in sheet[header_row]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="0F2F4C")
        rows = list(table.cells()) + ([table.total_cells()] if table.total else [])
        for index, row in enumerate(rows):
            sheet.append([_excel(c.text) for c in row])
            for cell, source in zip(sheet[sheet.max_row], row):
                if isinstance(source.text, str) and source.text.endswith("%") and source.text[:-1].isdigit():
                    cell.number_format = "0%"
                if table.total and index == len(rows) - 1:
                    cell.font = Font(bold=True)
        for i, _ in enumerate(table.columns, start=1):
            sheet.column_dimensions[sheet.cell(row=header_row, column=i).column_letter].width = 18
        sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    if not book.sheetnames:
        book.create_sheet("Report")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _excel(value):
    if value is None:
        return ""
    if isinstance(value, str) and value.endswith("%") and value[:-1].isdigit():
        return int(value[:-1]) / 100
    return value if isinstance(value, (int, float)) else str(value)


@transaction.atomic
def record_export(user, report, fmt, filters, row_count, sensitive):
    """Log every export; sensitive ones (learner-level data, R2) also go to the audit log."""
    export = ReportExport.objects.create(school=user.school, user=user, report=report, format=fmt,
                                         filters=filters, row_count=row_count, sensitive=sensitive)
    if sensitive:
        audit.record("report.export", export, actor=user, school=user.school,
                     changes={"report": report, "format": fmt, "filters": filters, "rows": row_count})
    return export
