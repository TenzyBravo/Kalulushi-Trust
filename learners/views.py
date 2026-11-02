import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import access

from . import forms, numbers, services
from .importer import ImportFailed, import_learners
from .models import Enrollment, GuardianContact, Learner, LearnerGuardian, LearnerSupportNote

TABS = ["personal", "guardians", "enrolment", "notes"]


def _to_tab(pk, tab):
    return redirect(f"{reverse('learner_detail', args=[pk])}?tab={tab}")


def _learner(request, pk):
    """The learner, if this user may see them. Others get a 404, so existence is not revealed."""
    learner = get_object_or_404(Learner.objects.select_related("school"), pk=pk, school_id=request.user.school_id)
    if not access.can_view_learner(request.user, learner):
        raise Http404
    return learner


def _form_page(request, form, title, learner=None, intro="", submit="Save", danger=False):
    return render(
        request,
        "form.html",
        {"form": form, "title": title, "learner": learner, "intro": intro, "submit": submit, "danger": danger},
    )


def _apply(form, action):
    """Run a service call; show its ValidationError on the form. Returns True on success."""
    try:
        action()
        return True
    except ValidationError as exc:
        form.add_error(None, exc)
        return False


def _guardian_choice(choice, school, phone):
    """Resolve the 'existing guardian with this number' question (decision D3).

    Returns (guardian or None, suggestions). Non-empty suggestions mean staff must
    choose between linking an existing guardian and creating a new one.
    """
    matches = list(services.guardians_with_phone(school, phone))
    if not matches or choice == "new":
        return None, []
    if choice and choice.isdigit():
        for guardian in matches:
            if guardian.pk == int(choice):
                return guardian, []
    return None, matches


@login_required
@permission_required("learners.view_learner", raise_exception=True)
def learner_list(request):
    query = request.GET.get("q", "").strip()
    learners = access.visible_learners(request.user)
    status = request.GET.get("status", "active")
    if status in dict(Learner.STATUSES):
        learners = learners.filter(status=status)
    classes = access.accessible_classes(request.user).filter(academic_year__status="current").select_related("grade")
    class_id = request.GET.get("class", "")
    if class_id.isdigit():
        learners = learners.filter(
            enrollments__school_class__in=classes.filter(pk=class_id), enrollments__status="active"
        ).distinct()
    hint = ""
    if query:
        if "-" in query and query.split("-")[0].isalpha():
            learners = learners.filter(learner_number__iexact=query)
            if not numbers.is_valid(query):
                hint = f"{query} is not a valid learner number. Check for a typing mistake."
        else:
            for part in query.split():
                learners = learners.filter(
                    Q(first_name__icontains=part) | Q(last_name__icontains=part) | Q(other_names__icontains=part)
                )
    page = Paginator(learners.order_by("last_name", "first_name"), 50).get_page(request.GET.get("page"))
    for learner in page:
        learner.enrollment = learner.current_enrollment()
    return render(
        request,
        "learners/list.html",
        {
            "page": page, "query": query, "status": status, "statuses": Learner.STATUSES, "hint": hint,
            "classes": classes.order_by("grade__sequence", "stream"), "class_id": class_id,
        },
    )


@login_required
@permission_required("learners.add_learner", raise_exception=True)
def register(request):
    school = request.user.school
    form = forms.RegistrationForm(request.POST or None, school=school)
    context = {"form": form}
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        primary, primary_matches = _guardian_choice(data["guardian_choice"], school, data["guardian_phone"])
        alt, alt_matches = (None, [])
        if data["alt_name"]:
            alt, alt_matches = _guardian_choice(data["alt_choice"], school, data["alt_phone"])
        if primary_matches or alt_matches:
            context.update(primary_matches=primary_matches, alt_matches=alt_matches)
            return render(request, "learners/register.html", context)

        guardians = [
            services.GuardianInput(
                relationship=data["guardian_relationship"],
                contact_role="primary",
                existing=primary,
                full_name=data["guardian_name"],
                phone=data["guardian_phone"],
                email=data["guardian_email"],
            )
        ]
        if data["alt_name"]:
            guardians.append(
                services.GuardianInput(
                    relationship=data["alt_relationship"],
                    contact_role="alternative",
                    existing=alt,
                    full_name=data["alt_name"],
                    phone=data["alt_phone"],
                )
            )
        try:
            learner = services.register(
                request.user,
                school_class=data["school_class"],
                guardians=guardians,
                override_reason=data["override_reason"],
                registration_date=data["registration_date"],
                **{f: data[f] for f in forms.LearnerForm.Meta.fields},
            )
        except services.LikelyDuplicate as dup:
            context.update(
                duplicates=dup.matches, can_override=request.user.has_perm("learners.override_duplicate")
            )
            return render(request, "learners/register.html", context)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Registered {learner.full_name} as {learner.learner_number}.")
            return redirect("learner_detail", pk=learner.pk)
    return render(request, "learners/register.html", context)


@login_required
def learner_detail(request, pk):
    learner = _learner(request, pk)
    tab = request.GET.get("tab", "personal")
    if tab not in TABS:
        tab = "personal"
    can_notes = access.can_view_support_notes(request.user, learner)
    context = {"learner": learner, "tab": tab, "can_notes": can_notes}
    if tab == "personal":
        context["enrollment"] = learner.current_enrollment()
    elif tab == "guardians":
        links = list(learner.active_guardian_links().prefetch_related("guardian__contacts"))
        for link in links:
            link.contact = next((c for c in link.guardian.contacts.all() if c.is_current), None)
            link.siblings = (
                Learner.objects.filter(guardian_links__guardian=link.guardian, guardian_links__status="active")
                .exclude(pk=learner.pk)
                .filter(pk__in=access.visible_learners(request.user))
            )
        context["links"] = links
        context["removed_links"] = learner.guardian_links.filter(status="removed").select_related("guardian")
    elif tab == "enrolment":
        context["enrollments"] = learner.enrollments.select_related("school_class", "academic_year", "created_by")
    elif tab == "notes":
        if not can_notes:
            raise Http404
        context["notes"] = services.read_notes(request.user, learner)
    return render(request, "learners/detail.html", context)


@login_required
@permission_required("learners.change_learner", raise_exception=True)
def edit(request, pk):
    learner = _learner(request, pk)
    form = forms.LearnerForm(request.POST or None, instance=Learner.objects.get(pk=learner.pk))
    if request.method == "POST" and form.is_valid():
        data = {f: form.cleaned_data[f] for f in forms.LearnerForm.Meta.fields}
        if _apply(form, lambda: services.update(request.user, learner, **data)):
            messages.success(request, "Saved.")
            return redirect("learner_detail", pk=pk)
    return _form_page(request, form, "Edit personal details", learner)


@login_required
@permission_required("learners.change_learner", raise_exception=True)
def change_status(request, pk):
    learner = _learner(request, pk)
    form = forms.StatusForm(request.POST or None, initial={"status": learner.status})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if _apply(form, lambda: services.change_status(request.user, learner, d["status"], d["reason"], d["effective_date"])):
            messages.success(request, "Status updated.")
            return redirect("learner_detail", pk=pk)
    return _form_page(
        request, form, "Change status", learner,
        intro="Leaving the school ends the learner's current enrolment the day before the effective date.",
    )


@login_required
@permission_required("learners.change_learner", raise_exception=True)
def add_guardian(request, pk):
    learner = _learner(request, pk)
    form = forms.AddGuardianForm(request.POST or None)
    matches = []
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        existing, matches = _guardian_choice(d["guardian_choice"], learner.school, d["phone"])
        if not matches:
            data = services.GuardianInput(
                relationship=d["relationship"], contact_role=d["contact_role"], receives_results=d["receives_results"],
                existing=existing, full_name=d["full_name"], phone=d["phone"], email=d["email"],
            )
            if _apply(form, lambda: services.add_guardian(request.user, learner, data)):
                messages.success(request, "Guardian added.")
                return _to_tab(pk, "guardians")
    return render(request, "learners/add_guardian.html", {"form": form, "learner": learner, "matches": matches})


def _link(request, pk, link_pk):
    learner = _learner(request, pk)
    return learner, get_object_or_404(LearnerGuardian, pk=link_pk, learner=learner, status="active")


@login_required
@permission_required("learners.change_learnerguardian", raise_exception=True)
def edit_link(request, pk, link_pk):
    learner, link = _link(request, pk, link_pk)
    form = forms.LinkForm(request.POST or None, instance=LearnerGuardian.objects.get(pk=link.pk))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        services.update_link(request.user, link, relationship=d["relationship"], receives_results=d["receives_results"])
        messages.success(request, "Saved.")
        return _to_tab(pk, "guardians")
    return _form_page(request, form, f"{link.guardian} – relationship and results", learner)


@login_required
@require_POST
@permission_required("learners.change_learnerguardian", raise_exception=True)
def set_contact_role(request, pk, link_pk):
    learner, link = _link(request, pk, link_pk)
    role = request.POST.get("role")
    if role not in dict(LearnerGuardian.ROLES):
        raise Http404
    try:
        services.set_contact_role(request.user, link, role)
        messages.success(request, f"{link.guardian} is now: {dict(LearnerGuardian.ROLES)[role].lower()}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return _to_tab(pk, "guardians")


@login_required
@permission_required("learners.change_learnerguardian", raise_exception=True)
def remove_link(request, pk, link_pk):
    learner, link = _link(request, pk, link_pk)
    form = forms.ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _apply(form, lambda: services.remove_link(request.user, link, form.cleaned_data["reason"])):
            messages.success(request, f"{link.guardian} is no longer linked to {learner.first_name}.")
            return _to_tab(pk, "guardians")
    return _form_page(
        request, form, f"Remove {link.guardian}", learner, submit="Remove guardian", danger=True,
        intro="The link is kept in the history. The guardian stays linked to any other learners.",
    )


@login_required
@permission_required("learners.change_guardian", raise_exception=True)
def edit_guardian(request, pk, link_pk):
    learner, link = _link(request, pk, link_pk)
    guardian = link.guardian
    form = forms.GuardianForm(request.POST or None, initial={"full_name": guardian.full_name, "email": guardian.email})
    if request.method == "POST" and form.is_valid():
        if _apply(form, lambda: services.update_guardian(request.user, guardian, **form.cleaned_data)):
            messages.success(request, "Saved.")
            return _to_tab(pk, "guardians")
    return _form_page(request, form, f"Edit {guardian}", learner, intro="Changes apply to every learner this guardian is linked to.")


@login_required
@permission_required("learners.change_guardiancontact", raise_exception=True)
def change_phone(request, pk, link_pk):
    learner, link = _link(request, pk, link_pk)
    form = forms.PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _apply(form, lambda: services.change_phone(request.user, link.guardian, form.cleaned_data["phone"])):
            messages.success(request, "Number changed. It needs verifying before results are sent to it.")
            return _to_tab(pk, "guardians")
    return _form_page(request, form, f"Change {link.guardian}'s number", learner)


@login_required
@permission_required("learners.change_guardiancontact", raise_exception=True)
def verify_phone(request, pk, link_pk):
    learner, link = _link(request, pk, link_pk)
    contact = get_object_or_404(GuardianContact, guardian=link.guardian, is_current=True)
    form = forms.VerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _apply(form, lambda: services.verify_contact(request.user, contact, form.cleaned_data["method"])):
            messages.success(request, f"{contact.phone} verified.")
            return _to_tab(pk, "guardians")
    return _form_page(request, form, f"Verify {contact.phone} for {link.guardian}", learner, submit="Mark verified")


def _enrollment(request, pk, enrollment_pk):
    learner = _learner(request, pk)
    return learner, get_object_or_404(Enrollment, pk=enrollment_pk, learner=learner, status="active")


@login_required
@permission_required("learners.change_enrollment", raise_exception=True)
def transfer(request, pk, enrollment_pk):
    learner, enrollment = _enrollment(request, pk, enrollment_pk)
    form = forms.TransferForm(request.POST or None, enrollment=enrollment)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if _apply(form, lambda: services.transfer(request.user, enrollment, d["school_class"], d["effective_date"], d["reason"])):
            messages.success(request, f"Moved to {d['school_class'].name}.")
            return _to_tab(pk, "enrolment")
    return _form_page(
        request, form, f"Move from {enrollment.school_class.name} to another class", learner,
        intro="Attendance and results before the effective date stay with the old class.",
    )


@login_required
@permission_required("learners.change_enrollment", raise_exception=True)
def correct(request, pk, enrollment_pk):
    learner, enrollment = _enrollment(request, pk, enrollment_pk)
    form = forms.CorrectForm(request.POST or None, enrollment=enrollment)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if _apply(form, lambda: services.correct(request.user, enrollment, d["school_class"], d["reason"])):
            messages.success(request, f"Corrected to {d['school_class'].name}.")
            return _to_tab(pk, "enrolment")
    return _form_page(
        request, form, f"Correct a mistaken enrolment in {enrollment.school_class.name}", learner, danger=True,
        intro="Use this only if the learner was put in the wrong class by mistake. "
        "The mistaken enrolment is marked reversed and kept in the history.",
    )


@login_required
@permission_required("learners.add_enrollment", raise_exception=True)
def enroll(request, pk):
    learner = _learner(request, pk)
    form = forms.EnrollForm(request.POST or None, school=learner.school)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if _apply(form, lambda: services.enroll(request.user, learner, d["school_class"], d["start_date"], d["reason"])):
            messages.success(request, f"Enrolled in {d['school_class'].name}.")
            return _to_tab(pk, "enrolment")
    return _form_page(request, form, "Enrol in a class", learner)


@login_required
@permission_required("learners.add_learnersupportnote", raise_exception=True)
def add_note(request, pk):
    learner = _learner(request, pk)
    form = forms.NoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if _apply(form, lambda: services.add_note(request.user, learner, d["category"], d["note"])):
            messages.success(request, "Note added.")
            return _to_tab(pk, "notes")
    return _form_page(
        request, form, "Add a support note", learner,
        intro="Restricted information. Keep to what staff need to know to care for the learner.",
    )


@login_required
@require_POST
@permission_required("learners.change_learnersupportnote", raise_exception=True)
def archive_note(request, pk, note_pk):
    learner = _learner(request, pk)
    services.archive_note(request.user, get_object_or_404(LearnerSupportNote, pk=note_pk, learner=learner))
    messages.success(request, "Note archived.")
    return _to_tab(pk, "notes")


@login_required
@permission_required("learners.add_learner", raise_exception=True)
def import_view(request):
    form = forms.ImportForm(request.POST or None, request.FILES or None)
    context = {"form": form}
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            text = io.TextIOWrapper(d["file"].file, encoding="utf-8-sig", newline="")
            result = import_learners(request.user, text, dry_run=d["dry_run"], override_reason=d["override_reason"])
        except ImportFailed as exc:
            context["errors"] = exc.errors
        except UnicodeDecodeError:
            context["errors"] = ["The file is not UTF-8 text. In Excel, save it as 'CSV UTF-8'."]
        else:
            context.update(result=result, dry_run=d["dry_run"])
            if not d["dry_run"]:
                messages.success(request, f"Registered {len(result.learners)} learner(s).")
    return render(request, "learners/import.html", context)
