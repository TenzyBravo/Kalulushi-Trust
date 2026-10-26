from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from school.models import AcademicYear, ClassGroup, ClassSubject

from . import services
from .forms import AssignTeacherForm, ClassTeacherForm, CoverForm
from .models import CoverAuthorization, TeacherAssignment


def _fail(form, exc):
    form.add_error(None, exc.messages[0] if isinstance(exc, ValidationError) else "That clashes with an existing assignment.")


@login_required
@permission_required("staffing.view_teacherassignment", raise_exception=True)
def overview(request):
    year = AcademicYear.current()
    class_id = request.GET.get("class")
    classes = ClassGroup.objects.filter(academic_year=year).select_related("grade") if year else ClassGroup.objects.none()
    selected = classes.filter(pk=class_id).first() if class_id else classes.first()
    subjects = []
    if selected:
        today = timezone.localdate()
        for cs in selected.class_subjects.select_related("subject"):
            current = cs.teacher_assignments.filter(status="active").exclude(end_date__lt=today).select_related("teacher")
            subjects.append((cs, current))
    return render(request, "staffing/overview.html", {
        "year": year, "classes": classes, "selected": selected, "subjects": subjects,
        "class_teacher": selected.class_teachers.filter(end_date__isnull=True).select_related("teacher").first() if selected else None,
        "covers": CoverAuthorization.objects.filter(revoked_at__isnull=True, ends_at__gt=timezone.now()).select_related(
            "cover_teacher", "teacher_assignment__teacher", "teacher_assignment__class_subject__class_group",
            "teacher_assignment__class_subject__subject"),
        "can_change": request.user.has_perm("staffing.add_teacherassignment"),
    })


@login_required
@permission_required("staffing.add_teacherassignment", raise_exception=True)
def assign(request, class_subject_pk):
    cs = get_object_or_404(ClassSubject.objects.select_related("class_group__academic_year", "subject"), pk=class_subject_pk)
    form = AssignTeacherForm(request.POST or None, initial={"start_date": timezone.localdate()})
    back = f"/assignments/?class={cs.class_group_id}"
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.assign_teacher(cs, d["teacher"], d["assignment_type"], d["start_date"], request.user)
        except (ValidationError, IntegrityError) as exc:
            _fail(form, exc)
        else:
            messages.success(request, f"{d['teacher']} assigned to {cs}.")
            return redirect(back)
    return render(request, "form.html", {"form": form, "title": f"Assign a teacher to {cs}", "cancel": back,
                                         "intro": "A primary teacher works with the class. A backup gets access only through a cover authorization."})


@login_required
@require_POST
@permission_required("staffing.change_teacherassignment", raise_exception=True)
def end(request, pk):
    assignment = get_object_or_404(TeacherAssignment, pk=pk)
    services.end_assignment(assignment, request.user, reason=request.POST.get("reason", ""))
    messages.success(request, "Assignment ended.")
    return redirect(f"/assignments/?class={assignment.class_subject.class_group_id}")


@login_required
@permission_required("staffing.add_classteacherassignment", raise_exception=True)
def class_teacher(request, class_pk):
    class_group = get_object_or_404(ClassGroup, pk=class_pk)
    form = ClassTeacherForm(request.POST or None, initial={"start_date": timezone.localdate()})
    back = f"/assignments/?class={class_group.pk}"
    if request.method == "POST" and form.is_valid():
        try:
            services.assign_class_teacher(class_group, form.cleaned_data["teacher"], form.cleaned_data["start_date"], request.user)
        except (ValidationError, IntegrityError) as exc:
            _fail(form, exc)
        else:
            messages.success(request, "Class teacher set.")
            return redirect(back)
    return render(request, "form.html", {"form": form, "title": f"Class teacher for {class_group}", "cancel": back,
                                         "intro": "The class teacher receives absence alerts. Any current class teacher is ended the day before."})


@login_required
@permission_required("staffing.add_coverauthorization", raise_exception=True)
def cover(request, assignment_pk):
    primary = get_object_or_404(TeacherAssignment.objects.select_related("class_subject__class_group"), pk=assignment_pk)
    form = CoverForm(request.POST or None, primary=primary)
    back = f"/assignments/?class={primary.class_subject.class_group_id}"
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.authorize_cover(primary, d["cover_teacher"], d["starts_at"], d["ends_at"], d["duties"], d["reason"], request.user)
        except ValidationError as exc:
            _fail(form, exc)
        else:
            messages.success(request, "Cover authorised. It ends automatically.")
            return redirect(back)
    return render(request, "form.html", {"form": form, "title": f"Cover for {primary.teacher} – {primary.class_subject}",
                                         "cancel": back, "intro": "Only teachers named as backup for this class subject can be chosen."})


@login_required
@require_POST
@permission_required("staffing.change_coverauthorization", raise_exception=True)
def cover_revoke(request, pk):
    cover_auth = get_object_or_404(CoverAuthorization, pk=pk)
    services.revoke_cover(cover_auth, request.user, reason=request.POST.get("reason", ""))
    messages.success(request, "Cover revoked.")
    return redirect("assignments")


