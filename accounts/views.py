from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from learners.models import Learner, LearnerGuardian
from school.models import AcademicYear

from . import access, roles, services
from .forms import DeactivateForm, NewStaffForm, RolesForm, StaffForm
from .models import TeacherAssignment, User, UserSession


@login_required
def home(request):
    user = request.user
    # None means "not shown for this user"; the template tests for it explicitly.
    context = {"year": AcademicYear.current_for(user.school), "learner_count": None, "classes": None, "unverified": None}
    if user.has_perm("learners.view_learner"):
        context["learner_count"] = access.visible_learners(user).filter(status="active").count()
    if not access.sees_all_learners(user):
        context["classes"] = access.accessible_classes(user).select_related("academic_year")
        context["assignments"] = (
            TeacherAssignment.in_force()
            .filter(teacher=user)
            .select_related("class_subject__school_class", "class_subject__subject")
        )
    if user.has_perm("learners.view_all_learners"):
        # One filter() call, so all four conditions apply to the same link and contact row.
        verified = LearnerGuardian.objects.filter(
            status="active",
            contact_role="primary",
            guardian__contacts__is_current=True,
            guardian__contacts__verified_at__isnull=False,
        ).values("learner_id")
        context["unverified"] = (
            Learner.objects.filter(school=user.school, status="active").exclude(pk__in=verified).count()
        )
    return render(request, "home.html", context)


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def staff_list(request):
    show = request.GET.get("show", "active")
    staff = User.objects.filter(school=request.user.school).prefetch_related("groups")
    if show != "all":
        staff = staff.filter(is_active=True)
    return render(
        request,
        "accounts/staff_list.html",
        {"staff": staff, "show": show, "can_add": bool(roles.assignable_roles(request.user)) and request.user.has_perm("accounts.add_user")},
    )


@login_required
@permission_required("accounts.add_user", raise_exception=True)
def staff_create(request):
    allowed = roles.assignable_roles(request.user)
    if not allowed:
        raise PermissionDenied
    form = NewStaffForm(request.POST or None, allowed_roles=allowed)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            user = services.create_staff(
                request.user,
                role_names=d["roles"],
                password=d["password"],
                **{f: d[f] for f in StaffForm.Meta.fields},
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Created an account for {user}. They can sign in as {user.username}.")
            return redirect("staff_detail", pk=user.pk)
    return render(request, "form.html", {"form": form, "title": "New staff account", "submit": "Create account"})


@login_required
def staff_detail(request, pk):
    if pk != request.user.pk and not request.user.has_perm("accounts.view_user"):
        raise PermissionDenied
    member = get_object_or_404(User, pk=pk, school=request.user.school)
    manageable = roles.can_manage(request.user, member)
    context = {
        "member": member,
        "roles": sorted(member.role_names()),
        "manageable": manageable,
        "can_deactivate": manageable and request.user.has_perm("accounts.deactivate_user") and member.is_active,
        "can_see_sessions": request.user.pk == member.pk or request.user.has_perm("accounts.view_usersession"),
    }
    if context["can_see_sessions"]:
        context["sessions"] = member.sessions.all()
    context["assignments"] = member.teacher_assignments.select_related(
        "class_subject__school_class__academic_year", "class_subject__subject"
    )[:50]
    return render(request, "accounts/staff_detail.html", context)


@login_required
@permission_required("accounts.change_user", raise_exception=True)
def staff_edit(request, pk):
    member = get_object_or_404(User, pk=pk, school=request.user.school)
    if not (request.user.pk == member.pk or roles.can_manage(request.user, member)):
        raise PermissionDenied
    form = StaffForm(request.POST or None, instance=User.objects.get(pk=member.pk))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_staff(request.user, member, **{f: form.cleaned_data[f] for f in StaffForm.Meta.fields})
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Saved.")
            return redirect("staff_detail", pk=pk)
    return render(request, "form.html", {"form": form, "title": f"Edit {member}"})


@login_required
def staff_roles(request, pk):
    member = get_object_or_404(User, pk=pk, school=request.user.school)
    if not roles.can_manage(request.user, member):
        raise PermissionDenied
    form = RolesForm(request.POST or None, allowed_roles=roles.assignable_roles(request.user), current=member.role_names())
    if request.method == "POST" and form.is_valid():
        try:
            services.set_roles(request.user, member, form.cleaned_data["roles"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Roles updated.")
            return redirect("staff_detail", pk=pk)
    return render(request, "form.html", {"form": form, "title": f"Roles for {member}"})


@login_required
@permission_required("accounts.deactivate_user", raise_exception=True)
def staff_deactivate(request, pk):
    member = get_object_or_404(User, pk=pk, school=request.user.school)
    if not roles.can_manage(request.user, member):
        raise PermissionDenied
    form = DeactivateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.deactivate(request.user, member, form.cleaned_data["reason"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"{member} has been deactivated and signed out everywhere.")
            return redirect("staff_detail", pk=pk)
    return render(
        request,
        "form.html",
        {
            "form": form,
            "title": f"Deactivate {member}",
            "intro": "This signs them out everywhere, ends their teaching and class-teacher assignments, "
            "and revokes any cover. Their records and history are kept.",
            "submit": "Deactivate account",
            "danger": True,
        },
    )


@login_required
@require_POST
def revoke_session(request, pk, session_pk):
    session = get_object_or_404(UserSession, pk=session_pk, user_id=pk, user__school=request.user.school)
    services.revoke_session(request.user, session)
    messages.success(request, "Session ended.")
    return redirect("staff_detail", pk=pk)
