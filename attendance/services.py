"""Registers, absence cases and the calendar. Each change runs in one transaction with its audit record."""
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core import audit
from learners.models import Enrollment
from school.models import AcademicYear

from . import access
from .calendar import SchoolCalendar, for_days
from .models import (
    STATUSES, AbsenceCase, AbsenceCaseRecord, AttendanceRecord, AttendanceSession, CalendarEvent, FollowUpAction,
)

# How far either side of a date the absence check looks for a run of absences.
RUN_WINDOW = timedelta(days=120)


def day_label(day, year=True):
    """5 Mar 2026: the day without a leading zero, on every platform."""
    return f"{day.day} {day:%b %Y}" if year else f"{day.day} {day:%b}"


# Registers ------------------------------------------------------------------


@dataclass
class RegisterRow:
    learner: object
    enrollment: object
    record: object = None


def register_rows(class_group, day):
    """Learners enrolled in the class on that date (§4), with any record already taken."""
    enrollments = (
        Enrollment.objects.filter(class_group=class_group, start_date__lte=day)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).exclude(status="reversed")
        .select_related("learner").order_by("learner__last_name", "learner__first_name")
    )
    records = {
        r.learner_id: r for r in AttendanceRecord.objects.filter(session__class_group=class_group, session__date=day)
    }
    return [RegisterRow(e.learner, e, records.get(e.learner_id)) for e in enrollments]


def register_state(class_group, day, cal=None):
    """Why a register cannot be taken on this day, or '' if it can."""
    cal = cal or for_days(day, day, class_group.school)
    if not cal.is_school_day(day):
        return cal.non_school_reason(day)
    closure = cal.closure_for(class_group, day)
    if closure:
        return f"The register for {class_group.name} is closed from {day_label(closure.start_date)}: {closure.reason or closure.name}."
    return ""


def save_register(class_group, day, marks, actor, reason="", today=None):
    """Save a whole register. marks: {learner_id: (status, note)} for every enrolled learner."""
    today = today or timezone.localdate()
    if day > today:
        raise ValidationError("A register cannot be taken for a future date.")
    blocked = register_state(class_group, day)
    if blocked:
        raise ValidationError(blocked)
    with transaction.atomic():
        session = AttendanceSession.objects.select_for_update().filter(class_group=class_group, date=day).first()
        mode = access.register_access(actor, class_group, day, session is not None, today=today)
        if mode not in (access.EDIT, access.CORRECT):
            raise PermissionDenied("You cannot change this register.")
        if mode == access.CORRECT and not reason.strip():
            raise ValidationError("Give a reason for correcting this register.")
        rows = register_rows(class_group, day)
        if not rows:
            raise ValidationError(f"No learners are enrolled in {class_group.name} on this date.")
        expected = {row.learner.pk: row for row in rows}
        if set(marks) - set(expected):
            raise ValidationError("The register includes learners who are not in this class on this date. Reload the page.")
        unmarked = [row.learner.full_name for row in rows if (marks.get(row.learner.pk) or (None,))[0] not in STATUSES]
        if unmarked:
            raise ValidationError(f"Mark every learner before saving. Not yet marked: {', '.join(unmarked)}.")

        if session is None:
            session = AttendanceSession.objects.create(
                school=class_group.school, class_group=class_group, academic_year=class_group.academic_year,
                date=day, taken_by=actor, last_saved_by=actor,
            )
        else:
            session.last_saved_by = actor
            session.save(update_fields=["last_saved_by", "updated_at"])

        existing = {r.learner_id: r for r in session.records.all()}
        changes, to_check = {}, []
        for learner_id, row in expected.items():
            status, note = marks[learner_id]
            note = (note or "").strip()
            record = existing.get(learner_id)
            if record is None:
                record = AttendanceRecord.objects.create(
                    session=session, class_group=class_group, enrollment=row.enrollment, learner=row.learner,
                    status=status, note=note, recorded_by=actor,
                )
                changes[row.learner.learner_number] = [None, status]
                old = None
            elif record.status != status or record.note != note:
                old = record.status
                if old != status:
                    changes[row.learner.learner_number] = [old, status]
                record.status, record.note, record.recorded_by = status, note, actor
                record.save(update_fields=["status", "note", "recorded_by", "updated_at"])
            else:
                continue
            if status == AttendanceRecord.ABSENT or old == AttendanceRecord.ABSENT:
                to_check.append(record)
        audit.record(
            "attendance.correct" if mode == access.CORRECT else "attendance.save", session, school=class_group.school,
            changes={"class": class_group.name, "date": day, "statuses": changes}, reason=reason, actor=actor,
        )
        for record in to_check:
            check_absence_run(record, actor)
    return session


# Absence early warning (§5) ---------------------------------------------------


def _run_records(learner, day):
    """The learner's records near the day, on register days only, oldest first."""
    cal = SchoolCalendar(learner.school, day - RUN_WINDOW, day + RUN_WINDOW)
    records = (
        AttendanceRecord.objects.filter(learner=learner, session__date__range=(day - RUN_WINDOW, day + RUN_WINDOW))
        .select_related("session", "class_group").order_by("session__date")
    )
    return [r for r in records if cal.is_register_day(r.session.date, r.class_group)]


def check_absence_run(record, actor=None):
    """Open or extend a case when this record is part of two or more consecutive absences.

    Consecutive means consecutive register days on which the learner's class had a
    register: a day with no register neither extends nor breaks a run (decision A4).
    """
    learner = record.learner
    if record.status != AttendanceRecord.ABSENT:
        for link in record.case_links.select_related("case").filter(case__status__in=AbsenceCase.OPEN):
            _system_note(
                link.case,
                f"The absence on {day_label(record.session.date)} was changed to {record.status}. "
                "The case stays open until someone resolves it.",
            )
        return None
    records = _run_records(learner, record.session.date)
    index = next((i for i, r in enumerate(records) if r.pk == record.pk), None)
    if index is None:
        return None  # the date is no longer a register day (for example a later closure)
    start = end = index
    while start > 0 and records[start - 1].status == AttendanceRecord.ABSENT:
        start -= 1
    while end + 1 < len(records) and records[end + 1].status == AttendanceRecord.ABSENT:
        end += 1
    run = records[start:end + 1]
    if len(run) < 2:
        return None

    first, last = run[0].session.date, run[-1].session.date
    case = AbsenceCase.objects.select_for_update().filter(learner=learner).exclude(status=AbsenceCase.RESOLVED).first()
    if case is None:
        case = AbsenceCase.objects.create(
            school=learner.school, learner=learner, class_group=run[-1].class_group,
            assigned_teacher=access.class_teacher_on(run[-1].class_group, last),
            first_absent_date=first, last_absent_date=last, days_absent=len(run),
        )
        _system_note(case, f"Flagged after {len(run)} consecutive school days absent ({day_label(first, False)} to {day_label(last)}).")
        audit.record("absence_case.open", case, school=learner.school, actor=actor,
                     changes={"learner": learner.learner_number, "first": first, "last": last, "days": len(run)})
    else:
        before = audit.snapshot(case, ["first_absent_date", "last_absent_date", "days_absent"])
        if last >= case.last_absent_date:
            case.days_absent = len(run)  # the current run
        case.first_absent_date = min(case.first_absent_date, first)
        case.last_absent_date = max(case.last_absent_date, last)
        case.save(update_fields=["first_absent_date", "last_absent_date", "days_absent", "updated_at"])
        changes = audit.diff(before, audit.snapshot(case, ["first_absent_date", "last_absent_date", "days_absent"]))
        if changes:
            _system_note(case, f"Still absent: {case.days_absent} consecutive school days, most recently {day_label(last)}.")
            audit.record("absence_case.extend", case, school=learner.school, actor=actor, changes=changes)
    for r in run:
        AbsenceCaseRecord.objects.get_or_create(case=case, attendance_record=r)
    return case


def _system_note(case, text):
    return FollowUpAction.objects.create(case=case, kind="system_note", outcome=text)


# Follow-up actions (§5.2, §5.5) ------------------------------------------------

TRANSITIONS = {
    AbsenceCase.NEW: {AbsenceCase.FOLLOW_UP_PENDING, AbsenceCase.IN_PROGRESS, AbsenceCase.RESOLVED, AbsenceCase.ESCALATED},
    AbsenceCase.FOLLOW_UP_PENDING: {AbsenceCase.IN_PROGRESS, AbsenceCase.RESOLVED, AbsenceCase.ESCALATED},
    AbsenceCase.IN_PROGRESS: {AbsenceCase.FOLLOW_UP_PENDING, AbsenceCase.RESOLVED, AbsenceCase.ESCALATED},
    AbsenceCase.ESCALATED: {AbsenceCase.IN_PROGRESS, AbsenceCase.RESOLVED},
    AbsenceCase.RESOLVED: set(),
}


def _lock_case(case, actor):
    case = AbsenceCase.objects.select_for_update().get(pk=case.pk)
    if not access.can_work_on_case(actor, case):
        raise PermissionDenied("You cannot work on this case.")
    return case


@transaction.atomic
def add_contact(case, actor, *, contact_date, contact_method, outcome, reason_category="", reason_text="",
                next_action="", next_action_due=None, contacted_guardian=None):
    case = _lock_case(case, actor)
    if not case.is_open:
        raise ValidationError("This case is resolved. A new absence will open a new case.")
    if contact_date > timezone.localdate():
        raise ValidationError("The contact date cannot be in the future.")
    action = FollowUpAction(
        case=case, kind="contact", contact_date=contact_date, contact_method=contact_method, outcome=outcome.strip(),
        reason_category=reason_category, reason_text=reason_text.strip(), next_action=next_action.strip(),
        next_action_due=next_action_due, contacted_guardian=contacted_guardian, created_by=actor,
    )
    action.full_clean()
    action.save()
    audit.record("absence_case.contact", case, school=case.school, actor=actor,
                 changes={"method": contact_method, "date": contact_date})
    return action


@transaction.atomic
def add_note(case, actor, text):
    case = _lock_case(case, actor)
    if not text.strip():
        raise ValidationError("Write a note.")
    action = FollowUpAction.objects.create(case=case, kind="note", outcome=text.strip(), created_by=actor)
    audit.record("absence_case.note", case, school=case.school, actor=actor)
    return action


@transaction.atomic
def change_status(case, to, actor, note=""):
    """Move a case along (design §5.2). Resolving needs a note; leaving 'escalated' needs the Headteacher."""
    case = _lock_case(case, actor)
    old = case.status
    if to == old:
        return case
    if to not in TRANSITIONS[old]:
        raise ValidationError(f"A case cannot go from {old.replace('_', ' ')} to {to.replace('_', ' ')}.")
    if old == AbsenceCase.ESCALATED and not access.can_review_escalations(actor):
        raise PermissionDenied("Only the Headteacher can move an escalated case on.")
    now = timezone.now()
    kind = "status_change"
    if to == AbsenceCase.RESOLVED:
        if not note.strip():
            raise ValidationError("Say how the case was resolved.")
        case.resolved_at, case.resolved_by, case.resolution = now, actor, note.strip()
    if to == AbsenceCase.ESCALATED:
        if not note.strip():
            raise ValidationError("Say why the case needs the Headteacher.")
        case.escalated_at, case.escalated_by, case.escalation_reason = now, actor, note.strip()
        kind = "referral"
    case.status = to
    case.save()
    FollowUpAction.objects.create(case=case, kind=kind, status_from=old, status_to=to, outcome=note.strip(), created_by=actor)
    audit.record(f"absence_case.{'escalate' if kind == 'referral' else 'status'}", case, school=case.school, actor=actor,
                 changes={"status": [old, to]}, reason=note)
    return case


def last_activity(case):
    latest = case.actions.order_by("-created_at").first()
    return latest.created_at if latest else case.flagged_at


def is_stale(case, stale_days, today=None):
    """An open case with no action for stale_days school days (§5.3)."""
    if not case.is_open:
        return False
    today = today or timezone.localdate()
    since = timezone.localdate(last_activity(case))
    return len(for_days(since, today, case.school).school_days_between(since, today)) >= stale_days


# Calendar (§3) ------------------------------------------------------------------


def _year_for(school, day):
    year = AcademicYear.objects.filter(school=school, start_date__lte=day, end_date__gte=day).first()
    if year is None:
        raise ValidationError(f"{day_label(day)} is not inside any academic year.")
    return year


@transaction.atomic
def add_event(actor, *, kind, name, start_date, end_date=None, reason="", grade=None, class_group=None, school=None):
    needed = "attendance.add_closure" if kind == CalendarEvent.CLOSURE else "attendance.add_calendarevent"
    if not actor.has_perm(needed):
        raise PermissionDenied
    school = school or actor.school
    year = _year_for(school, start_date)
    if end_date and not year.contains(end_date):
        raise ValidationError(f"The event must end inside academic year {year}.")
    if class_group is not None and class_group.academic_year_id != year.pk:
        raise ValidationError(f"Class {class_group.name} belongs to {class_group.academic_year}, not {year}.")
    if kind != CalendarEvent.CLOSURE:
        grade = class_group = None
    if end_date is None and grade is None and class_group is None:
        end_date = start_date
    event = CalendarEvent(
        school=school, academic_year=year, kind=kind, name=name.strip(), start_date=start_date, end_date=end_date,
        reason=reason.strip(), grade=grade, class_group=class_group, created_by=actor,
    )
    event.full_clean()
    event.save()
    audit.record("calendar_event.create", event, school=school, actor=actor, reason=reason, changes={
        "kind": kind, "name": event.name, "start": start_date, "end": end_date, "scope": event.scope_label,
    })
    return event


@transaction.atomic
def end_event(event, end_date, actor, reason=""):
    """End a closure early, e.g. a class recalled after its exams (§3.1)."""
    needed = "attendance.add_closure" if event.kind == CalendarEvent.CLOSURE else "attendance.change_calendarevent"
    if not actor.has_perm(needed):
        raise PermissionDenied
    event = CalendarEvent.objects.select_for_update().get(pk=event.pk)
    if end_date < event.start_date:
        raise ValidationError("The end date cannot be before the start date.")
    if not event.academic_year.contains(end_date):
        raise ValidationError(f"The end date must fall inside academic year {event.academic_year}.")
    old = event.end_date
    event.end_date = end_date
    event.save(update_fields=["end_date", "updated_at"])
    audit.record("calendar_event.end", event, school=event.school, actor=actor, reason=reason,
                 changes={"end_date": [old, end_date]})
    return event
