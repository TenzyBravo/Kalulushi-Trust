from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from school.models import AcademicYear, ClassGroup
from staffing.access import sees_whole_school

from . import promotions
from .models import EnrollmentRequest, PromotionBatch

R = EnrollmentRequest
DECISION_CHOICES = [(R.PROMOTE, "Promote"), (R.REPEAT, "Repeat"), (R.HOLD, "Hold for review"),
                    (R.COMPLETE, "Complete (final grade)"), (R.LEAVING, "Leaving the school")]


def _messages(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc) or "Not allowed."]


def _can_open(user):
    return user.is_active and (user.has_perm("learners.prepare_promotions") or promotions.can_approve(user))


def _learner_context(class_group, learners):
    """Latest term's overall result and the year's attendance rate, to help decide (design §5)."""
    from assessment import calc
    from attendance.models import AttendanceRecord

    year = class_group.academic_year
    term = year.terms.filter(start_date__lte=timezone.localdate()).order_by("-start_date").first() or year.terms.order_by("start_date").first()
    overall = {}
    if term:
        _, rows = calc.class_term_results(class_group, term, learners)
        overall = {r.learner.pk: r.overall for r in rows}
    rates = {}
    counts = (AttendanceRecord.objects.filter(learner__in=learners, session__academic_year=year)
              .values("learner").annotate(total=Count("id"), attended=Count("id", filter=Q(status__in=["present", "late"]))))
    for c in counts:
        rates[c["learner"]] = round(100 * c["attended"] / c["total"]) if c["total"] else None
    return term, overall, rates


@login_required
def index(request):
    user = request.user
    if not _can_open(user):
        raise PermissionDenied
    year = AcademicYear.current(user.school)
    to_year = promotions.next_year(year) if year else None
    classes = list(year.classes.filter(status="active").select_related("grade")) if year else []
    if not sees_whole_school(user):
        classes = [c for c in classes if promotions.can_prepare(user, c)]
    batches = {b.from_class_id: b for b in PromotionBatch.objects.filter(from_class__in=classes)}
    return render(request, "promotions/index.html", {
        "year": year, "to_year": to_year, "rows": [(c, batches.get(c.pk)) for c in classes],
        "can_approve": promotions.can_approve(user), "pending": promotions.pending_counts(user),
    })


@login_required
def sheet(request, pk):
    class_group = get_object_or_404(ClassGroup.objects.select_related("grade", "academic_year", "school"), pk=pk)
    user = request.user
    can_prepare = promotions.can_prepare(user, class_group)
    if not (can_prepare or promotions.can_approve(user) or (sees_whole_school(user) and _can_open(user))):
        raise Http404
    to_year = promotions.next_year(class_group.academic_year)
    batch = promotions.batch_for(class_group)
    errors = []
    if request.method == "POST":
        if not can_prepare:
            raise PermissionDenied
        try:
            batch = batch or promotions.batch_for(class_group, actor=user, create=True)
            to_classes = {str(c.pk): c for c in ClassGroup.objects.filter(academic_year=batch.to_year)}
            entries = {}
            for enrollment, _ in promotions.sheet_rows(batch):
                lid = enrollment.learner_id
                entries[lid] = (request.POST.get(f"decision_{lid}", ""), to_classes.get(request.POST.get(f"class_{lid}", "")),
                                request.POST.get(f"note_{lid}", ""))
            promotions.save_sheet(user, batch, entries)
            if request.POST.get("action") == "submit":
                promotions.submit_sheet(user, batch)
                messages.success(request, f"{class_group.name}'s promotion sheet is with the Headteacher.")
                return redirect("promotions")
            messages.success(request, "Saved.")
            return redirect("promotion_sheet", pk=class_group.pk)
        except (ValidationError, PermissionDenied) as exc:
            errors = _messages(exc)
    if batch:
        rows = promotions.sheet_rows(batch)
    elif to_year:
        decision, destination = promotions.suggested(class_group, to_year)
        rows = [(e, R(decision=decision, to_class=destination)) for e in promotions.year_end_roster(class_group)]
    else:
        rows = []
    term, overall, rates = _learner_context(class_group, [e.learner for e, _ in rows])
    editable = can_prepare and to_year is not None and (batch is None or batch.status in (PromotionBatch.DRAFT, PromotionBatch.RETURNED))
    return render(request, "promotions/sheet.html", {
        "class_group": class_group, "to_year": to_year, "batch": batch, "editable": editable, "errors": errors,
        "rows": [(e, r, overall.get(e.learner_id), rates.get(e.learner_id)) for e, r in rows], "term": term,
        "decisions": DECISION_CHOICES, "to_classes": ClassGroup.objects.filter(academic_year=to_year, status="active").select_related("grade") if to_year else [],
    })


@login_required
def approvals(request):
    user = request.user
    if not promotions.can_approve(user):
        raise PermissionDenied
    sheets = PromotionBatch.objects.filter(status=PromotionBatch.SUBMITTED).select_related("from_class", "prepared_by")
    transfers = R.objects.filter(kind=R.TRANSFER, status=R.PENDING).select_related(
        "learner", "from_enrollment__class_group", "to_class", "requested_by")
    return render(request, "promotions/approvals.html", {
        "sheets": [(b, promotions.summary(b)) for b in sheets], "transfers": transfers, "held": promotions.held_requests(),
    })


@login_required
def review(request, pk):
    batch = get_object_or_404(PromotionBatch.objects.select_related("from_class__grade", "to_year", "prepared_by"), pk=pk)
    user = request.user
    if not promotions.can_approve(user):
        raise PermissionDenied
    errors = []
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "approve":
                if request.POST.get("confirm") != "yes":
                    raise ValidationError("Tick the box to confirm before approving.")
                promotions.approve_sheet(user, batch)
                messages.success(request, f"{batch.from_class.name} promoted: new enrolments start on {batch.to_year.start_date}.")
                return redirect("promotion_approvals")
            if action == "return":
                promotions.return_sheet(user, batch, request.POST.get("note", ""))
                messages.success(request, f"{batch.from_class.name}'s sheet returned to {batch.prepared_by}.")
                return redirect("promotion_approvals")
        except (ValidationError, PermissionDenied) as exc:
            errors = _messages(exc)
    requests = batch.requests.select_related("learner", "to_class").order_by("decision", "learner__last_name")
    return render(request, "promotions/review.html", {
        "batch": batch, "summary": promotions.summary(batch), "requests": requests, "errors": errors,
    })


class DecisionNoteForm(forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


@login_required
@require_POST
def transfer_decide(request, pk):
    transfer = get_object_or_404(R, pk=pk, kind=R.TRANSFER)
    note = request.POST.get("note", "")
    try:
        if request.POST.get("action") == "approve":
            promotions.approve_transfer(request.user, transfer, note)
            messages.success(request, f"Transfer approved: {transfer.learner.full_name} to {transfer.to_class.name}.")
        elif request.POST.get("action") == "reject":
            promotions.reject_transfer(request.user, transfer, note)
            messages.success(request, "Transfer rejected.")
        elif request.POST.get("action") == "cancel":
            promotions.cancel_transfer(request.user, transfer)
            messages.success(request, "Request cancelled.")
            return redirect(f"{reverse('learner_profile', args=[transfer.learner_id])}?tab=enrollments")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, " ".join(_messages(exc)))
    return redirect("promotion_approvals")


class PlaceForm(forms.Form):
    to_class = forms.ModelChoiceField(queryset=ClassGroup.objects.none(), label="Class next year")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_class"].queryset = ClassGroup.objects.filter(academic_year=year, status="active").select_related("grade")


@login_required
def place(request, pk):
    held = get_object_or_404(R.objects.select_related("learner", "batch__to_year", "batch__from_class"), pk=pk)
    if not promotions.can_approve(request.user):
        raise PermissionDenied
    form = PlaceForm(request.POST or None, year=held.batch.to_year)
    if request.method == "POST" and form.is_valid():
        try:
            promotions.place_held(request.user, held, form.cleaned_data["to_class"], form.cleaned_data["reason"])
        except (ValidationError, PermissionDenied) as exc:
            for m in _messages(exc):
                form.add_error(None, m)
        else:
            messages.success(request, f"{held.learner.full_name} placed in {form.cleaned_data['to_class'].name}.")
            return redirect("promotion_approvals")
    return render(request, "form.html", {
        "form": form, "title": f"Decide for {held.learner.full_name} (held in {held.batch.from_class.name})",
        "intro": f"Held because: {held.note}", "cancel": reverse("promotion_approvals"),
    })
