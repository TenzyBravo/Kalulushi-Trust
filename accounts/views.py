from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from attendance import dashboard as attendance_dashboard
from learners.models import Enrollment, Guardian, GuardianContact, Learner
from school.models import AcademicYear
from staffing import access
from staffing.models import ClassTeacherAssignment, CoverAuthorization, TeacherAssignment

from . import permission_catalogue, roles, services
from .forms import PasswordResetForm, ReasonForm, StaffForm
from .models import User, UserSession


@login_required
def home(request):
    user = request.user
    year = AcademicYear.current()
    ctx = {"year": year, "whole_school": access.sees_whole_school(user), "today": timezone.localdate()}
    if user.has_perm("learners.view_learner"):
        visible = access.visible_learners(user, Learner.objects.filter(status="active"))
        ctx["learner_count"] = visible.count()
    if ctx["whole_school"]:
        learners_url = reverse("learner_list")
        unverified = GuardianContact.objects.filter(is_current=True, verified_at__isnull=True)
        no_primary = Learner.objects.filter(status="active").exclude(
            guardian_links__status="active", guardian_links__contact_role="primary")
        not_enrolled = Learner.objects.filter(status="active").exclude(
            enrollments__status="active", enrollments__end_date__isnull=True)
        # (count, label, link). Only items with a count show in "Waiting on you".
        ctx["attention"] = [
            (unverified.count(), "guardian numbers to verify", learners_url),
            (no_primary.count(), "learners without a primary contact", learners_url),
            (not_enrolled.count(), "learners not in a class", learners_url),
        ]
        if year:
            ctx["classes"] = (year.classes.filter(status="active").select_related("grade")
                              .annotate(size=Count("enrollments", filter=Q(enrollments__status="active", enrollments__end_date__isnull=True))))
        else:
            ctx["attention"].insert(0, (1, "academic year to make current", reverse("admin:school_academicyear_changelist")))
        ctx["glance"] = [
            {"value": ctx.get("learner_count", 0), "label": "Active learners", "tone": "green", "icon": "people",
             "text": "Learners currently at the school", "link": learners_url, "link_text": "View learners"},
            {"value": Enrollment.objects.filter(academic_year=year, status="active").count() if year else 0,
             "label": f"Enrolments ({year})" if year else "Enrolments", "tone": "navy", "icon": "calendar",
             "text": "Learners placed in a class this academic year", "link": learners_url, "link_text": "Browse by class"},
            {"value": Guardian.objects.filter(status="active").count(), "label": "Guardians", "tone": "mint", "icon": "home",
             "text": "Parents and guardians on record", "link": learners_url, "link_text": "Find a family"},
            {"value": unverified.count(), "label": "Numbers to verify", "tone": "rose", "icon": "phone",
             "text": "Guardian phones not yet confirmed, needed before results go by SMS", "link": learners_url,
             "link_text": "Start verifying"},
            {"value": len(ctx["classes"]) if year else 0, "label": "Classes", "tone": "violet", "icon": "building",
             "text": f"Active classes in {year}" if year else "No academic year is current yet",
             "link": reverse("assignments") if user.has_perm("staffing.view_teacherassignment") else None,
             "link_text": "Teacher assignments"},
            {"value": User.objects.filter(is_active=True).count(), "label": "Staff", "tone": "amber", "icon": "badge",
             "text": "Active staff accounts", "link": reverse("staff_list") if user.has_perm("accounts.view_user") else None,
             "link_text": "Manage staff"},
        ]
    if user.has_role(roles.TEACHER):
        today = timezone.localdate()
        ctx["my_subjects"] = TeacherAssignment.objects.filter(
            teacher=user, status="active", start_date__lte=today
        ).exclude(end_date__lt=today).select_related("class_subject__class_group", "class_subject__subject")
        ctx["my_class"] = ClassTeacherAssignment.objects.filter(teacher=user, end_date__isnull=True).select_related("class_group").first()
        ctx["my_covers"] = CoverAuthorization.objects.filter(
            cover_teacher=user, revoked_at__isnull=True, ends_at__gt=timezone.now()
        ).select_related("teacher_assignment__class_subject__class_group", "teacher_assignment__class_subject__subject")
    # Registers and absence cases (slice 2) for everyone who works with attendance.
    ctx["attention"] = ctx.get("attention", []) + attendance_dashboard.attention_items(user) + promotion_items(user)
    ctx["attention_total"] = sum(count for count, _, _ in ctx["attention"])
    return render(request, "home.html", ctx)


def promotion_items(user):
    """Promotion sheets and transfers waiting (promotions design §5)."""
    from learners import promotions
    from learners.models import PromotionBatch

    items = []
    pending = promotions.pending_counts(user)
    if pending:
        items.append((pending["sheets"], "promotion sheets to approve", reverse("promotion_approvals")))
        items.append((pending["transfers"], "transfer requests to approve", reverse("promotion_approvals")))
    year = AcademicYear.current(user.school)
    if year and not access.sees_whole_school(user) and promotions.next_year(year):
        for c in year.classes.filter(status="active"):
            if promotions.is_class_teacher_of(user, c) and not PromotionBatch.objects.filter(
                    from_class=c, status__in=[PromotionBatch.SUBMITTED, PromotionBatch.APPROVED]).exists():
                items.append((1, f"promotion sheet for {c.name} to submit", reverse("promotion_sheet", args=[c.pk])))
    return items


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
    can_manage = request.user.has_perm("accounts.manage_staff") and roles.can_manage(request.user, person)
    extra = []
    if can_manage or request.user.pk == person.pk:
        direct = {permission_catalogue.code_of(p) for p in person.user_permissions.select_related("content_type")}
        for code in permission_catalogue.GRANTABLE:
            permission = permission_catalogue.get_permission(code)
            extra.append({
                "code": code, "label": permission_catalogue.label_of(permission),
                "description": permission_catalogue.DESCRIPTIONS.get(code, ""),
                "sensitive": code in permission_catalogue.SENSITIVE,
                "direct": code in direct, "via_role": services.role_grants(person, code),
                "can_change": can_manage and request.user.has_perm(code),
            })
    return render(request, "staff/detail.html", {
        "person": person,
        "extra_permissions": extra,
        "can_manage": can_manage,
        "sessions": UserSession.objects.filter(user=person),
        "assignments": person.teacher_assignments.select_related("class_subject__class_group", "class_subject__subject").order_by("status", "-start_date"),
        "class_teacher": person.class_teacher_assignments.select_related("class_group"),
    })


@login_required
@permission_required("accounts.manage_staff", raise_exception=True)
@require_POST
def staff_permission(request, pk):
    """Grant or remove one extra permission; every change is audited with its reason."""
    person = get_object_or_404(User, pk=pk)
    code, action, reason = request.POST.get("code", ""), request.POST.get("action"), request.POST.get("reason", "")
    try:
        if action == "grant":
            services.grant_permission(request.user, person, code, reason)
            messages.success(request, f"{person} can now: {code.split('.', 1)[1].replace('_', ' ')}.")
        elif action == "revoke":
            services.revoke_permission(request.user, person, code, reason)
            messages.success(request, f"Removed {code.split('.', 1)[1].replace('_', ' ')} from {person}.")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, exc.messages[0] if isinstance(exc, ValidationError) else (str(exc) or "Not allowed."))
    return redirect(f"{reverse('staff_detail', args=[person.pk])}#permissions")


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def permission_list(request):
    return render(request, "staff/permissions.html", {
        "modules": permission_catalogue.catalogue(), "roles": roles.ALL_ROLES,
        "type_labels": permission_catalogue.TYPE_LABELS,
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


