from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import School
from school.models import AcademicYear, ClassGroup
from staffing import access

from . import importer, numbers, services
from .duplicates import guardians_with_phone
from .forms import (
    EnrollForm, GuardianEditForm, GuardianLinkForm, ImportConfirmForm, ImportUploadForm, LearnerEditForm,
    PhoneChangeForm, ReasonForm, RegistrationForm, StatusForm, SupportNoteForm, TransferForm, VerifyForm,
)
from .models import Enrollment, Guardian, GuardianContact, Learner, LearnerGuardian, LearnerSupportNote

TABS = [
    ("personal", "Personal details", None),
    ("guardians", "Guardians and contacts", None),
    ("enrollments", "Enrolment and class history", None),
    ("attendance", "Attendance", "module 2"),
    ("results", "Academic results", "module 3"),
    ("competencies", "Competency development", "module 3"),
    ("reports", "Report cards", "module 4"),
    ("followup", "Absence follow-up", "module 2"),
]
IMPORT_SESSION_KEY = "learner_import_rows"


def _messages_from(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc)]


def _visible_learner(request, pk):
    learner = get_object_or_404(Learner.objects.select_related("school"), pk=pk)
    if not access.can_view_learner(request.user, learner):
        raise Http404  # don't reveal learners outside the user's classes
    return learner


def _profile_url(learner, tab="personal"):
    return f"{reverse('learner_profile', args=[learner.pk])}?tab={tab}"


@login_required
@permission_required("learners.view_learner", raise_exception=True)
def learner_list(request):
    q = request.GET.get("q", "").strip()
    class_id = request.GET.get("class", "")
    status = request.GET.get("status", "active")
    learners = access.visible_learners(request.user, Learner.objects.all())
    hint = ""
    if q:
        if numbers.looks_like_number(q):
            if not numbers.is_valid(q):
                hint = f"'{q}' is not a valid learner number: the last digit doesn't match. Check for a typing mistake."
            learners = learners.filter(learner_number__iexact=q)
        else:
            for word in q.split():
                learners = learners.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(other_names__icontains=word))
    if class_id:
        learners = learners.filter(enrollments__class_group_id=class_id, enrollments__status="active",
                                   enrollments__end_date__isnull=True)
    if status:
        learners = learners.filter(status=status)
    year = AcademicYear.current()
    classes = ClassGroup.objects.filter(academic_year=year).select_related("grade") if year else []
    if not access.sees_whole_school(request.user):
        classes = [c for c in classes if c.pk in access.class_ids(request.user)]
    page = list(learners.distinct().order_by("last_name", "first_name")[:300])
    current = {e.learner_id: e for e in Enrollment.objects.filter(learner__in=page, status="active", end_date__isnull=True).select_related("class_group")}
    rows = [(l, current.get(l.pk)) for l in page]
    return render(request, "learners/list.html", {
        "rows": rows, "q": q, "hint": hint, "classes": classes, "class_id": class_id, "status": status,
        "statuses": Learner.STATUSES,
    })


@login_required
@permission_required("learners.add_learner", raise_exception=True)
def register(request):
    school = School.current()
    form = RegistrationForm(request.POST or None)
    ctx = {"form": form, "year": AcademicYear.current()}
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        choice = d.get("guardian_choice")
        suggestions = list(guardians_with_phone(school, d["guardian_phone"]))
        if suggestions and not choice:
            ctx["guardian_suggestions"] = suggestions  # D3: staff confirm before linking
            return render(request, "learners/register.html", ctx)
        existing = None
        if choice and choice != "new":
            existing = get_object_or_404(Guardian, pk=choice, school=school)
        reg = services.Registration(
            first_name=d["first_name"], last_name=d["last_name"], other_names=d["other_names"],
            date_of_birth=d["date_of_birth"], gender=d["gender"], previous_school=d["previous_school"],
            class_group=d["class_group"], start_date=d["start_date"],
            guardian=services.GuardianInput(d["guardian_name"], d["guardian_phone"], d["relationship"],
                                            d["guardian_email"], existing=existing),
            support_notes=[(d["support_category"], d["support_note"])] if d["support_note"] else [],
        )
        try:
            learner = services.register_learner(reg, request.user, override_reason=d["override_reason"], school=school)
        except services.DuplicateWarning as warning:
            ctx["duplicates"] = warning.matches
            ctx["can_override"] = request.user.has_perm("learners.override_duplicate")
            ctx["guardian_suggestions"] = suggestions if choice else None
            return render(request, "learners/register.html", ctx)
        except (ValidationError, PermissionDenied) as exc:
            for m in _messages_from(exc):
                form.add_error(None, m)
            return render(request, "learners/register.html", ctx)
        messages.success(request, f"Registered {learner.full_name} as {learner.learner_number}.")
        return redirect(_profile_url(learner))
    return render(request, "learners/register.html", ctx)


@login_required
def profile(request, pk):
    learner = _visible_learner(request, pk)
    tab = request.GET.get("tab", "personal")
    if tab not in [t for t, _, _ in TABS]:
        tab = "personal"
    user = request.user
    ctx = {"learner": learner, "tab": tab, "tabs": TABS, "current": learner.current_enrollment()}
    if tab == "personal":
        ctx["can_see_notes"] = access.can_view_support_notes(user, learner)
        if ctx["can_see_notes"] and request.GET.get("notes") == "show":
            ctx["notes"] = services.view_support_notes(learner, user)  # audited
        ctx["note_count"] = learner.support_notes.filter(archived_at__isnull=True).count() if ctx["can_see_notes"] else None
    elif tab == "guardians":
        links = learner.guardian_links.select_related("guardian").order_by("status", "contact_role", "created_at")
        ctx["links"] = [(l, l.guardian.contacts.all()) for l in links]
    elif tab == "enrollments":
        ctx["enrollments"] = learner.enrollments.select_related("class_group", "academic_year", "created_by", "approved_by")
    else:
        ctx["coming_in"] = dict((t, m) for t, _, m in TABS)[tab]
    return render(request, "learners/profile.html", ctx)


@login_required
@permission_required("learners.change_learner", raise_exception=True)
def edit(request, pk):
    learner = _visible_learner(request, pk)
    initial = {f: getattr(learner, f) for f in services.LEARNER_FIELDS}
    form = LearnerEditForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        services.update_learner(learner, form.cleaned_data, request.user)
        messages.success(request, "Saved.")
        return redirect(_profile_url(learner))
    return render(request, "form.html", {"form": form, "title": f"Edit {learner.full_name}", "cancel": _profile_url(learner)})


@login_required
@permission_required("learners.change_learner", raise_exception=True)
def change_status(request, pk):
    learner = _visible_learner(request, pk)
    form = StatusForm(request.POST or None, initial={"status": learner.status})
    if request.method == "POST" and form.is_valid():
        services.change_status(learner, form.cleaned_data["status"], form.cleaned_data["reason"], request.user)
        messages.success(request, f"Status is now {form.cleaned_data['status']}.")
        return redirect(_profile_url(learner))
    return render(request, "form.html", {
        "form": form, "title": f"Change status of {learner.full_name}", "cancel": _profile_url(learner),
        "intro": "Records are never deleted. A learner who leaves keeps their full history.",
    })


@login_required
@permission_required("learners.add_learnersupportnote", raise_exception=True)
def note_add(request, pk):
    learner = _visible_learner(request, pk)
    form = SupportNoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.add_support_note(learner, form.cleaned_data["category"], form.cleaned_data["note"], request.user)
        messages.success(request, "Note added.")
        return redirect(_profile_url(learner))
    return render(request, "form.html", {"form": form, "title": "Add medical or support note", "cancel": _profile_url(learner),
                                         "intro": "Only authorised staff can read notes, and every reading is logged."})


@login_required
@require_POST
@permission_required("learners.change_learnersupportnote", raise_exception=True)
def note_archive(request, pk, note_pk):
    learner = _visible_learner(request, pk)
    note = get_object_or_404(LearnerSupportNote, pk=note_pk, learner=learner)
    services.archive_support_note(note, request.user)
    messages.success(request, "Note archived.")
    return redirect(_profile_url(learner) + "&notes=show")


# Guardians ---------------------------------------------------------------------------------

@login_required
@permission_required("learners.add_learnerguardian", raise_exception=True)
def guardian_add(request, pk):
    learner = _visible_learner(request, pk)
    form = GuardianLinkForm(request.POST or None)
    ctx = {"form": form, "learner": learner}
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        suggestions = list(guardians_with_phone(learner.school, d["phone"]).exclude(
            learner_links__learner=learner, learner_links__status="active"))
        if suggestions and not d["guardian_choice"]:
            ctx["guardian_suggestions"] = suggestions
            return render(request, "learners/guardian_add.html", ctx)
        try:
            if d["guardian_choice"] and d["guardian_choice"] != "new":
                guardian = get_object_or_404(Guardian, pk=d["guardian_choice"], school=learner.school)
            else:
                guardian = services.create_guardian(learner.school, d["full_name"], d["phone"], d["email"], request.user)
            services.link_guardian(learner, guardian, request.user, relationship=d["relationship"],
                                   contact_role=d["contact_role"], receives_results=d["receives_results"])
        except ValidationError as exc:
            for m in exc.messages:
                form.add_error(None, m)
            return render(request, "learners/guardian_add.html", ctx)
        messages.success(request, f"Linked {guardian.full_name}.")
        return redirect(_profile_url(learner, "guardians"))
    return render(request, "learners/guardian_add.html", ctx)


@login_required
@require_POST
@permission_required("learners.change_learnerguardian", raise_exception=True)
def link_action(request, pk, link_pk):
    learner = _visible_learner(request, pk)
    link = get_object_or_404(LearnerGuardian, pk=link_pk, learner=learner, status="active")
    action = request.POST.get("action")
    if action in ("primary", "alternative", "none"):
        services.set_contact_role(link, action, request.user)
    elif action in ("results_on", "results_off"):
        services.set_receives_results(link, action == "results_on", request.user)
    messages.success(request, "Saved.")
    return redirect(_profile_url(learner, "guardians"))


@login_required
@permission_required("learners.change_learnerguardian", raise_exception=True)
def link_remove(request, pk, link_pk):
    learner = _visible_learner(request, pk)
    link = get_object_or_404(LearnerGuardian, pk=link_pk, learner=learner, status="active")
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.remove_link(link, form.cleaned_data["reason"], request.user)
        messages.success(request, f"Removed {link.guardian.full_name} from {learner.full_name}.")
        return redirect(_profile_url(learner, "guardians"))
    return render(request, "form.html", {
        "form": form, "title": f"Remove {link.guardian.full_name}", "cancel": _profile_url(learner, "guardians"),
        "intro": "The guardian stops being a contact for this learner. Their history and other children are kept.",
        "danger": True,
    })


@login_required
@permission_required("learners.change_guardian", raise_exception=True)
def guardian_edit(request, pk, guardian_pk):
    learner = _visible_learner(request, pk)
    guardian = get_object_or_404(Guardian, pk=guardian_pk, learner_links__learner=learner)
    form = GuardianEditForm(request.POST or None, initial={"full_name": guardian.full_name, "email": guardian.email})
    if request.method == "POST" and form.is_valid():
        services.update_guardian(guardian, form.cleaned_data["full_name"], form.cleaned_data["email"], request.user)
        messages.success(request, "Saved.")
        return redirect(_profile_url(learner, "guardians"))
    return render(request, "form.html", {"form": form, "title": f"Edit {guardian.full_name}", "cancel": _profile_url(learner, "guardians"),
                                         "intro": f"Changes apply to every learner linked to {guardian.full_name}."})


@login_required
@permission_required("learners.change_guardiancontact", raise_exception=True)
def phone_change(request, pk, guardian_pk):
    learner = _visible_learner(request, pk)
    guardian = get_object_or_404(Guardian, pk=guardian_pk, learner_links__learner=learner)
    form = PhoneChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.change_phone(guardian, form.cleaned_data["phone"], request.user, form.cleaned_data["reason"])
        messages.success(request, "Number changed. The new number needs verifying.")
        return redirect(_profile_url(learner, "guardians"))
    return render(request, "form.html", {
        "form": form, "title": f"Change mobile for {guardian.full_name}", "cancel": _profile_url(learner, "guardians"),
        "intro": f"Current: {guardian.current_phone or 'none'}. The old number is kept in history.",
    })


@login_required
@permission_required("learners.change_guardiancontact", raise_exception=True)
def contact_verify(request, pk, contact_pk):
    learner = _visible_learner(request, pk)
    contact = get_object_or_404(GuardianContact, pk=contact_pk, guardian__learner_links__learner=learner, is_current=True)
    form = VerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.verify_contact(contact, form.cleaned_data["method"], request.user)
        messages.success(request, f"{contact.phone} marked as verified.")
        return redirect(_profile_url(learner, "guardians"))
    return render(request, "form.html", {
        "form": form, "title": f"Verify {contact.phone}", "cancel": _profile_url(learner, "guardians"),
        "intro": "Confirm you spoke to the guardian on this number. Verification by SMS code comes with the SMS module.",
    })


# Enrollments -------------------------------------------------------------------------------

@login_required
@permission_required("learners.transfer_learner", raise_exception=True)
def transfer(request, pk):
    learner = _visible_learner(request, pk)
    current = learner.current_enrollment()
    if current is None:
        messages.error(request, "This learner has no current enrolment.")
        return redirect(_profile_url(learner, "enrollments"))
    form = TransferForm(request.POST or None, year=current.academic_year, exclude=current.class_group_id)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.transfer(learner, d["to_class"], d["effective_date"], d["reason"], request.user)
        except ValidationError as exc:
            for m in exc.messages:
                form.add_error(None, m)
        else:
            messages.success(request, f"Transferred to {d['to_class'].name} from {d['effective_date']}.")
            return redirect(_profile_url(learner, "enrollments"))
    return render(request, "form.html", {
        "form": form, "title": f"Transfer {learner.full_name} from {current.class_group.name}",
        "cancel": _profile_url(learner, "enrollments"),
        "intro": "Records before the transfer stay with the old class; records from the effective date belong to the new one.",
    })


@login_required
@permission_required("learners.change_enrollment", raise_exception=True)
def enrollment_reverse(request, pk, enrollment_pk):
    learner = _visible_learner(request, pk)
    enrollment = get_object_or_404(Enrollment, pk=enrollment_pk, learner=learner)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.reverse_enrollment(enrollment, form.cleaned_data["reason"], request.user)
        messages.success(request, "Enrolment reversed. Add the correct enrolment below.")
        return redirect(reverse("learner_enroll", args=[learner.pk]))
    return render(request, "form.html", {
        "form": form, "title": f"Reverse enrolment in {enrollment.class_group.name}",
        "cancel": _profile_url(learner, "enrollments"), "danger": True,
        "intro": "Use this only to correct a mistake. The reversed record stays in the history.",
    })


@login_required
@permission_required("learners.add_enrollment", raise_exception=True)
def enroll(request, pk):
    learner = _visible_learner(request, pk)
    form = EnrollForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.enroll(learner, d["class_group"], d["start_date"], d["reason"], request.user)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, exc.messages[0] if isinstance(exc, ValidationError) else
                           "This learner already has an enrolment covering that date.")
        else:
            messages.success(request, "Enrolled.")
            return redirect(_profile_url(learner, "enrollments"))
    return render(request, "form.html", {"form": form, "title": f"Enrol {learner.full_name}",
                                         "cancel": _profile_url(learner, "enrollments")})


# Bulk import -------------------------------------------------------------------------------

@login_required
@permission_required("learners.import_learners", raise_exception=True)
def import_template(request):
    response = HttpResponse(importer.build_template(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="learner-import-template.xlsx"'
    return response


@login_required
@permission_required("learners.import_learners", raise_exception=True)
def import_upload(request):
    form = ImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            rows = importer.read_upload(form.cleaned_data["file"])
        except importer.ImportFileError as exc:
            form.add_error("file", str(exc))
        else:
            request.session[IMPORT_SESSION_KEY] = rows
            return redirect("learner_import_preview")
    return render(request, "learners/import_upload.html", {"form": form, "year": AcademicYear.current()})


@login_required
@permission_required("learners.import_learners", raise_exception=True)
def import_preview(request):
    rows = request.session.get(IMPORT_SESSION_KEY)
    if not rows:
        return redirect("learner_import")
    school = School.current()
    form = ImportConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = importer.confirm(rows, school, request.user, override_reason=form.cleaned_data["override_reason"])
        except PermissionDenied as exc:
            form.add_error("override_reason", str(exc))
        else:
            leftover = result.invalid + result.skipped_duplicates
            request.session[IMPORT_SESSION_KEY] = [(r.line, r.raw) for r in leftover]
            return render(request, "learners/import_done.html", {"result": result, "leftover": leftover})
    preview = importer.validate(rows, school)
    return render(request, "learners/import_preview.html", {
        "preview": preview, "form": form, "can_override": request.user.has_perm("learners.override_duplicate"),
    })


@login_required
@permission_required("learners.import_learners", raise_exception=True)
def import_errors(request):
    rows = request.session.get(IMPORT_SESSION_KEY) or []
    preview = importer.validate(rows, School.current())
    problem_rows = preview.invalid + preview.flagged
    response = HttpResponse(importer.error_report(problem_rows),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="learner-import-problems.xlsx"'
    return response
