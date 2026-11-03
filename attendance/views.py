from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import SchoolSettings
from learners.models import Guardian
from school.models import AcademicYear, ClassGroup
from staffing.access import sees_whole_school

from . import access, calendar_import, services
from .calendar import for_days
from .forms import CalendarUploadForm, ClosureForm, ContactForm, EndEventForm, EventForm, NoteForm, StatusForm
from .models import STATUSES, AbsenceCase, AttendanceSession, CalendarEvent


def _parse_day(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _messages(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc) or "Not allowed."]


def _register_url(class_group, day):
    return f"{reverse('attendance_register', args=[class_group.pk])}?date={day.isoformat()}"


def class_day_status(class_groups, day, school):
    """For each class: taken (with the session), not taken, closed (reason) or not a school day."""
    cal = for_days(day, day, school)
    sessions = {
        s.class_group_id: s
        for s in AttendanceSession.objects.filter(class_group__in=class_groups, date=day).select_related("taken_by")
        .annotate(absent=Count("records", filter=Q(records__status="absent")), total=Count("records"))
    }
    rows = []
    for c in class_groups:
        state = services.register_state(c, day, cal)
        session = sessions.get(c.pk)
        rows.append({
            "class_group": c,
            "session": session,
            "state": "taken" if session else ("closed" if state else "not_taken"),
            "reason": state,
            "teacher": access.class_teacher_on(c, day),
            "url": _register_url(c, day),
        })
    return rows


@login_required
@permission_required("attendance.view_attendancesession", raise_exception=True)
def today(request):
    """My classes (class teachers) and, for office roles, every class's register for the day."""
    user = request.user
    day = _parse_day(request.GET.get("date"), timezone.localdate())
    school = user.school
    cal = for_days(day, day, school)
    ctx = {"day": day, "school_day": cal.is_school_day(day), "non_school_reason": cal.non_school_reason(day),
           "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1), "today": timezone.localdate()}
    ctx["mine"] = class_day_status(list(access.register_classes(user, day)), day, school)
    if sees_whole_school(user):
        year = AcademicYear.current(school)
        classes = list(year.classes.filter(status="active").select_related("grade")) if year else []
        ctx["overview"] = class_day_status(classes, day, school)
        ctx["not_taken"] = sum(1 for r in ctx["overview"] if r["state"] == "not_taken")
    return render(request, "attendance/today.html", ctx)


@login_required
@permission_required("attendance.view_attendancesession", raise_exception=True)
def register(request, pk):
    class_group = get_object_or_404(ClassGroup.objects.select_related("academic_year", "grade", "school"), pk=pk)
    user = request.user
    today_ = timezone.localdate()
    day = _parse_day(request.GET.get("date") or request.POST.get("date"), today_)
    session = AttendanceSession.objects.filter(class_group=class_group, date=day).select_related("taken_by", "last_saved_by").first()
    mode = access.register_access(user, class_group, day, session is not None, today=today_)
    if mode is None:
        raise Http404
    blocked = services.register_state(class_group, day) if day <= today_ else "A register cannot be taken for a future date."
    rows = services.register_rows(class_group, day)
    editable = mode in (access.EDIT, access.CORRECT) and not blocked and bool(rows)
    marks = {row.learner.pk: (row.record.status if row.record else "", row.record.note if row.record else "") for row in rows}
    errors, reason = [], ""

    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        marks = {
            row.learner.pk: (request.POST.get(f"status_{row.learner.pk}", ""), request.POST.get(f"note_{row.learner.pk}", ""))
            for row in rows
        }
        reason = request.POST.get("reason", "")
        try:
            services.save_register(class_group, day, marks, user, reason=reason, today=today_)
        except (ValidationError, PermissionDenied) as exc:
            errors = _messages(exc)
        else:
            messages.success(request, f"Register for {class_group.name} on {services.day_label(day)} saved.")
            return redirect(_register_url(class_group, day))

    counts = {s: sum(1 for st, _ in marks.values() if st == s) for s in STATUSES}
    no_alert = for_days(day, day, class_group.school).no_alert_day(day) if not blocked else None
    return render(request, "attendance/register.html", {
        "class_group": class_group, "day": day, "today": today_, "session": session, "mode": mode,
        "editable": editable, "blocked": blocked, "errors": errors, "reason": reason, "no_alert": no_alert,
        "rows": [(row, marks[row.learner.pk][0], marks[row.learner.pk][1]) for row in rows],
        "statuses": [(s, s[0].upper(), s.capitalize()) for s in STATUSES], "counts": counts,
        "unmarked": sum(1 for st, _ in marks.values() if st not in STATUSES),
        "back": reverse("attendance_today") + f"?date={day.isoformat()}",
    })


# Absence follow-up --------------------------------------------------------------


@login_required
@permission_required("attendance.view_absencecase", raise_exception=True)
def followup(request):
    user = request.user
    cases = access.visible_cases(user).select_related("learner", "class_group", "assigned_teacher").annotate(
        last_action=Max("actions__created_at"))
    status = request.GET.get("status", "open")
    if status == "open":
        cases = cases.exclude(status=AbsenceCase.RESOLVED)
    elif status in dict(AbsenceCase._meta.get_field("status").choices):
        cases = cases.filter(status=status)
    class_id = request.GET.get("class", "")
    if class_id.isdigit():
        cases = cases.filter(class_group_id=class_id)
    teacher_id = request.GET.get("teacher", "")
    if teacher_id.isdigit():
        cases = cases.filter(assigned_teacher_id=teacher_id)
    flagged_from = _parse_day(request.GET.get("from"), None)
    if flagged_from:
        cases = cases.filter(flagged_at__date__gte=flagged_from)
    stale_days = SchoolSettings.for_school(user.school).absence_stale_days
    cases = list(cases)
    for case in cases:
        case.stale = services.is_stale(case, stale_days)
    # Escalated first, then long-outstanding, then newest (§7 screen 4).
    cases.sort(key=lambda c: (c.status != AbsenceCase.ESCALATED, not c.stale, -c.last_absent_date.toordinal()))
    visible = access.visible_cases(user)
    return render(request, "attendance/followup_list.html", {
        "cases": cases, "status": status, "class_id": class_id, "teacher_id": teacher_id,
        "from": request.GET.get("from", ""), "stale_days": stale_days,
        "statuses": AbsenceCase._meta.get_field("status").choices,
        "classes": ClassGroup.objects.filter(pk__in=visible.values("class_group")).order_by("grade__sequence", "stream"),
        "teachers": {c.assigned_teacher for c in visible.select_related("assigned_teacher") if c.assigned_teacher},
    })


@login_required
@permission_required("attendance.view_absencecase", raise_exception=True)
def case_detail(request, pk):
    case = get_object_or_404(access.visible_cases(request.user).select_related("learner", "class_group", "assigned_teacher"), pk=pk)
    user = request.user
    guardians = Guardian.objects.filter(learner_links__learner=case.learner, learner_links__status="active").distinct()
    can_work = access.can_work_on_case(user, case)
    action = request.POST.get("action") if request.method == "POST" else None
    contact_form = ContactForm(request.POST if action == "contact" else None, guardians=guardians)
    note_form = NoteForm(request.POST if action == "note" else None)
    status_form = StatusForm(request.POST if action == "status" else None, initial={"status": case.status})
    if action:
        if not can_work:
            raise PermissionDenied
        form = {"contact": contact_form, "note": note_form, "status": status_form}.get(action)
        if form is not None and form.is_valid():
            d = form.cleaned_data
            try:
                if action == "contact":
                    guardian = guardians.filter(pk=d["contacted_guardian"]).first() if d["contacted_guardian"] else None
                    services.add_contact(
                        case, user, contact_date=d["contact_date"], contact_method=d["contact_method"],
                        outcome=d["outcome"], reason_category=d["reason_category"], reason_text=d["reason_text"],
                        next_action=d["next_action"], next_action_due=d["next_action_due"], contacted_guardian=guardian,
                    )
                elif action == "note":
                    services.add_note(case, user, d["text"])
                else:
                    services.change_status(case, d["status"], user, d["note"])
            except (ValidationError, PermissionDenied) as exc:
                for m in _messages(exc):
                    form.add_error(None, m)
            else:
                messages.success(request, "Saved to the case history.")
                return redirect("attendance_case", pk=case.pk)

    window_start = case.first_absent_date - timedelta(days=14)
    recent = (
        case.learner.attendance_records.filter(session__date__gte=window_start)
        .select_related("session", "class_group").order_by("-session__date")[:30]
    )
    linked = set(case.case_records.values_list("attendance_record_id", flat=True))
    return render(request, "attendance/case_detail.html", {
        "case": case, "actions": case.actions.select_related("created_by", "contacted_guardian"),
        "recent": recent, "linked": linked, "can_work": can_work,
        "can_review": access.can_review_escalations(user), "guardians": guardians,
        "contact_form": contact_form, "note_form": note_form, "status_form": status_form,
        "open_form": action or request.GET.get("form", ""),
    })


# Calendar -----------------------------------------------------------------------


@login_required
@permission_required("attendance.view_calendarevent", raise_exception=True)
def calendar(request):
    user = request.user
    year = AcademicYear.objects.filter(pk=request.GET.get("year")).first() if request.GET.get("year", "").isdigit() else None
    year = year or AcademicYear.current(user.school)
    add = request.POST.get("add") if request.method == "POST" else request.GET.get("add", "")
    event_form = EventForm(request.POST if add == "event" else None)
    closure_form = ClosureForm(request.POST if add == "closure" else None, year=year)
    if request.method == "POST":
        form = event_form if add == "event" else closure_form
        if form.is_valid():
            d = form.cleaned_data
            try:
                if add == "event":
                    services.add_event(user, kind=d["kind"], name=d["name"], start_date=d["start_date"],
                                       end_date=d["end_date"] or d["start_date"])
                else:
                    scope = d["scope"]
                    grade = d["grade"] if scope == "grade" else None
                    class_group = d["class_group"] if scope == "class" else None
                    target = grade.name if grade else (f"Class {class_group.name}" if class_group else "School")
                    services.add_event(user, kind=CalendarEvent.CLOSURE, name=d["name"] or f"{target} closed",
                                       start_date=d["start_date"], end_date=d["end_date"], reason=d["reason"],
                                       grade=grade, class_group=class_group)
            except (ValidationError, PermissionDenied) as exc:
                for m in _messages(exc):
                    form.add_error(None, m)
            else:
                messages.success(request, "Added to the calendar.")
                return redirect(reverse("attendance_calendar") + (f"?year={year.pk}" if year else ""))
    events = CalendarEvent.objects.filter(academic_year=year, superseded_at__isnull=True).select_related(
        "grade", "class_group", "created_by") if year else []
    return render(request, "attendance/calendar.html", {
        "year": year, "years": AcademicYear.objects.filter(school=user.school),
        "terms": year.terms.all() if year else [], "events": events, "add": add,
        "event_form": event_form, "closure_form": closure_form,
        "can_manage": user.has_perm("attendance.add_calendarevent"), "can_close": user.has_perm("attendance.add_closure"),
    })


@login_required
def calendar_end(request, pk):
    event = get_object_or_404(CalendarEvent, pk=pk)
    needed = "attendance.add_closure" if event.kind == CalendarEvent.CLOSURE else "attendance.change_calendarevent"
    if not request.user.has_perm(needed):
        raise PermissionDenied
    form = EndEventForm(request.POST or None, initial={"end_date": event.end_date or timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            services.end_event(event, form.cleaned_data["end_date"], request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            for m in exc.messages:
                form.add_error(None, m)
        else:
            messages.success(request, f"{event.name} now ends on {services.day_label(form.cleaned_data['end_date'])}.")
            return redirect(reverse("attendance_calendar") + f"?year={event.academic_year_id}")
    return render(request, "form.html", {
        "form": form, "title": f"Change the end of {event.name}", "cancel": reverse("attendance_calendar"),
        "intro": f"{event.scope_label}, from {services.day_label(event.start_date)}. Registers from the day after the new end date can be taken again.",
    })


# Calendar upload ------------------------------------------------------------------

CALENDAR_SESSION_KEY = "calendar_upload"


@login_required
@permission_required("attendance.add_calendarevent", raise_exception=True)
def calendar_template(request):
    response = HttpResponse(
        calendar_import.workbook(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="school-calendar-template.xlsx"'
    return response


@login_required
@permission_required("attendance.add_calendarevent", raise_exception=True)
def calendar_upload(request):
    """Upload the year's calendar: checked and previewed before anything is saved."""
    years = AcademicYear.objects.filter(school=request.user.school)
    current = AcademicYear.current(request.user.school)
    form = CalendarUploadForm(request.POST or None, request.FILES or None, years=years,
                              initial={"year": current.pk if current else None})
    preview = None
    if request.method == "POST" and request.POST.get("action") == "confirm":
        stored = request.session.get(CALENDAR_SESSION_KEY)
        year = years.filter(pk=(stored or {}).get("year")).first()
        if not stored or year is None:
            messages.error(request, "The upload expired. Choose the file again.")
            return redirect("attendance_calendar_upload")
        preview = calendar_import.from_session(stored["rows"], year)
        try:
            created, replaced = calendar_import.apply(preview, request.user)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(_messages(exc)))
        else:
            request.session.pop(CALENDAR_SESSION_KEY, None)
            messages.success(request, f"Calendar for {year} uploaded: {created} item(s) added"
                                      f"{f', {replaced} earlier item(s) replaced' if replaced else ''}.")
            return redirect(f"{reverse('attendance_calendar')}?year={year.pk}")
    elif request.method == "POST" and form.is_valid():
        year = form.cleaned_data["year"]
        try:
            preview = calendar_import.parse(form.cleaned_data["file"], form.cleaned_data["file"].name, year)
        except calendar_import.CalendarFileError as exc:
            form.add_error("file", str(exc))
        else:
            request.session[CALENDAR_SESSION_KEY] = {"year": year.pk, "rows": [r.as_session() for r in preview.rows]}
    return render(request, "attendance/calendar_upload.html", {"form": form, "preview": preview})
