from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from learners.models import GuardianContact, Learner
from school.models import AcademicYear
from staffing import access
from staffing.models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment

from . import roles, services
from .forms import PasswordResetForm, ReasonForm, StaffForm
from .models import User, UserSession


@login_required
def home(request):
    user = request.user
    year = AcademicYear.current()
    ctx = {"year": year, "whole_school": access.sees_whole_school(user)}
    if user.has_perm("learners.view_learner"):
        visible = access.visible_learners(user, Learner.objects.filter(status="active"))
        ctx["learner_count"] = visible.count()
    if ctx["whole_school"]:
        unverified = GuardianContact.objects.filter(is_current=True, verified_at__isnull=True)
        no_primary = Learner.objects.filter(status="active").exclude(
            guardian_links__status="active", guardian_links__contact_role="primary")
        not_enrolled = Learner.objects.filter(status="active").exclude(
            enrollments__status="active", enrollments__end_date__isnull=True)
        ctx["attention"] = [
            (unverified.count(), "guardian numbers not yet verified (needed before results SMS)", None),
            (no_primary.count(), "active learners with no primary contact", None),
            (not_enrolled.count(), "active learners not enrolled in a class", None),
        ]
        if year:
            ctx["classes"] = (year.classes.filter(status="active").select_related("grade")
                              .annotate(size=Count("enrollments", filter=Q(enrollments__status="active", enrollments__end_date__isnull=True))))
        else:
            ctx["attention"].insert(0, (1, "no academic year is current: set one in School setup", None))
    if user.has_role(roles.TEACHER):
        today = timezone.localdate()
        ctx["my_subjects"] = TeacherAssignment.objects.filter(
            teacher=user, status="active", start_date__lte=today
        ).exclude(end_date__lt=today).select_related("class_subject__class_group", "class_subject__subject")
        ctx["my_class"] = ClassTeacherAssignment.objects.filter(teacher=user, end_date__isnull=True).select_related("class_group").first()
        ctx["my_covers"] = CoverAuthorization.objects.filter(
            cover_teacher=user, revoked_at__isnull=True, ends_at__gt=timezone.now()
        ).select_related("teacher_assignment__class_subject__class_group", "teacher_assignment__class_subject__subject")
    return render(request, "home.html", ctx)


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def staff_list(request):
    show = request.GET.get("show", "active")
    users = User.objects.prefetch_related("groups").order_by("last_name", "first_name")
    users = users.filter(is_active=(show != "inactive"))
    return render(request, "staff/list.html", {"users": users, "show": show,
                                               "can_manage": request.user.has_perm("accounts.manage_staff")})


def _profile_data(cleaned):
    return {k: cleaned[k] for k in services.PROFILE_FIELDS}


@login_required
@permission_required("accounts.manage_staff", raise_exception=True)
def staff_create(request):
    form = StaffForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            user = services.create_staff(request.user, d["roles"], d["password"], **_profile_data(d))
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, str(exc) if isinstance(exc, PermissionDenied) else exc.messages[0])
        else:
            messages.success(request, f"Created {user}. They can sign in with {user.username} or {user.staff_number}.")
            return redirect("staff_detail", pk=user.pk)
    return render(request, "form.html", {"form": form, "title": "New staff account", "cancel": "/staff/"})


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def staff_detail(request, pk):
    person = get_object_or_404(User, pk=pk)
    return render(request, "staff/detail.html", {
        "person": person,
        "can_manage": request.user.has_perm("accounts.manage_staff") and roles.can_manage(request.user, person),
        "sessions": UserSession.objects.filter(user=person),
        "assignments": person.teacher_assignments.select_related("class_subject__class_group", "class_subject__subject").order_by("status", "-start_date"),
        "class_teacher": person.class_teacher_assignments.select_related("class_group"),
    })


@login_required
@permission_required("accounts.manage_staff", raise_exception=True)
def staff_edit(request, pk):
    person = get_object_or_404(User, pk=pk)
    if not roles.can_manage(request.user, person):
        raise PermissionDenied
    initial = {f: getattr(person, f) or "" for f in services.PROFILE_FIELDS} | {"roles": person.role_names}
    form = StaffForm(request.POST or None, actor=request.user, instance=person, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.update_staff(request.user, person, d["roles"], **_profile_data(d))
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, str(exc) if isinstance(exc, PermissionDenied) else exc.messages[0])
        else:
            messages.success(request, "Saved.")
            return redirect("staff_detail", pk=person.pk)
    return render(request, "form.html", {"form": form, "title": f"Edit {person}", "cancel": f"/staff/{person.pk}/"})


@login_required
@permission_required("accounts.manage_staff", raise_exception=True)
def staff_password(request, pk):
    person = get_object_or_404(User, pk=pk)
    if not roles.can_manage(request.user, person):
        raise PermissionDenied
    form = PasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.set_password(request.user, person, form.cleaned_data["password"])
        messages.success(request, f"Password changed. {person}'s other sessions were signed out.")
        return redirect("staff_detail", pk=person.pk)
    return render(request, "form.html", {"form": form, "title": f"Set a new password for {person}", "cancel": f"/staff/{person.pk}/"})


@login_required
@permission_required("accounts.deactivate_staff", raise_exception=True)
def staff_deactivate(request, pk):
    person = get_object_or_404(User, pk=pk)
    if not roles.can_manage(request.user, person):
        raise PermissionDenied
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.deactivate(request.user, person, form.cleaned_data["reason"])
        messages.success(request, f"{person} is deactivated and signed out everywhere.")
        return redirect("staff_detail", pk=person.pk)
    return render(request, "form.html", {
        "form": form, "title": f"Deactivate {person}", "cancel": f"/staff/{person.pk}/", "danger": True,
        "intro": "This signs them out immediately, ends their teaching and class-teacher assignments, and revokes any cover. Their records are kept.",
    })


@login_required
@require_POST
@permission_required("accounts.deactivate_staff", raise_exception=True)
def staff_reactivate(request, pk):
    person = get_object_or_404(User, pk=pk)
    services.reactivate(request.user, person, request.POST.get("reason", "Reactivated"))
    messages.success(request, f"{person} can sign in again. Assignments must be given again.")
    return redirect("staff_detail", pk=person.pk)


@login_required
@require_POST
def session_revoke(request, pk, session_pk):
    session = get_object_or_404(UserSession, pk=session_pk, user_id=pk)
    services.revoke_session(request.user, session)
    messages.success(request, "Session ended.")
    return redirect("staff_detail", pk=pk)


