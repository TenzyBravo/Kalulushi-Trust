"""Year-end promotion sheets and within-year transfer requests, approved by the Headteacher.

Promotions design (docs/database-design-promotions.md), decisions P1–P7.
"""
from collections import Counter
from dataclasses import dataclass, field
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core import audit
from school.models import AcademicYear, ClassGroup, Grade
from staffing.access import can_view_learner, sees_whole_school
from staffing.models import ClassTeacherAssignment

from . import services
from .models import Enrollment, EnrollmentRequest, Learner, PromotionBatch

R = EnrollmentRequest
NEEDS_NOTE = {R.REPEAT, R.HOLD, R.LEAVING}
END_REASONS = {R.PROMOTE: "promotion", R.REPEAT: "repeat", R.COMPLETE: "completion", R.LEAVING: "withdrawal"}


# Access (design §4) ------------------------------------------------------------


def can_approve(user):
    return user.is_active and user.has_perm("learners.approve_enrollment_requests")


def is_class_teacher_of(user, class_group):
    year_end = class_group.academic_year.end_date
    today = min(timezone.localdate(), year_end)
    return ClassTeacherAssignment.objects.filter(class_group=class_group, teacher=user, start_date__lte=today).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)).exists()


def can_prepare(user, class_group):
    """The class teacher, or an office role (decision P1)."""
    if not user.is_active or not user.has_perm("learners.prepare_promotions"):
        return False
    return sees_whole_school(user) or is_class_teacher_of(user, class_group)


def can_request_transfer(user, learner):
    """A teacher of the learner, or an office role (decision P6)."""
    if not user.is_active or not user.has_perm("learners.request_transfer"):
        return False
    return sees_whole_school(user) or can_view_learner(user, learner)


# Year-end promotions ------------------------------------------------------------


def next_year(year):
    """The academic year after this one, if it has been set up."""
    return AcademicYear.objects.filter(school=year.school, start_date__gt=year.end_date).order_by("start_date").first()


def year_end_roster(class_group):
    """Enrollments in the class on the last day of its year (status not reversed)."""
    year_end = class_group.academic_year.end_date
    return list(
        Enrollment.objects.filter(class_group=class_group, start_date__lte=year_end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=year_end)).exclude(status="reversed")
        .select_related("learner").order_by("learner__last_name", "learner__first_name")
    )


def next_grade(grade):
    return Grade.objects.filter(school=grade.school, sequence__gt=grade.sequence, status="active").order_by("sequence").first()


def suggested(class_group, to_year):
    """Default decision and destination (decision P2): same stream, next grade; complete in the final grade."""
    following = next_grade(class_group.grade)
    if following is None:
        return R.COMPLETE, None
    destination = ClassGroup.objects.filter(academic_year=to_year, grade=following, stream=class_group.stream, status="active").first()
    return R.PROMOTE, destination


def batch_for(class_group, actor=None, create=False):
    batch = PromotionBatch.objects.filter(from_class=class_group).select_related("to_year", "from_year").first()
    if batch or not create:
        return batch
    to_year = next_year(class_group.academic_year)
    if to_year is None:
        raise ValidationError(f"Set up the academic year after {class_group.academic_year}, with its classes, first.")
    with transaction.atomic():
        batch = PromotionBatch.objects.create(school=class_group.school, from_class=class_group,
                                              from_year=class_group.academic_year, to_year=to_year, prepared_by=actor)
        audit.record("promotion_batch.create", batch, actor=actor, school=batch.school)
    return batch


def sheet_rows(batch):
    """[(enrollment, request)] for everyone on the year-end roster, creating default draft decisions."""
    roster = year_end_roster(batch.from_class)
    requests = {r.learner_id: r for r in batch.requests.select_related("to_class")}
    default_decision, default_class = suggested(batch.from_class, batch.to_year)
    rows = []
    for enrollment in roster:
        request = requests.get(enrollment.learner_id)
        if request is None:
            request = R(school=batch.school, kind=R.PROMOTION, batch=batch, learner=enrollment.learner,
                        from_enrollment=enrollment, decision=default_decision, to_class=default_class,
                        effective_date=batch.to_year.start_date, requested_by=batch.prepared_by)
        rows.append((enrollment, request))
    return rows


def _check_request(batch, request):
    """Problems with one learner's decision (design §3, Validation)."""
    if request.decision not in dict(R._meta.get_field("decision").choices) or not request.decision:
        return "choose a decision"
    if request.decision in NEEDS_NOTE and not request.note.strip():
        return f"add a note for '{request.decision}'"
    if request.decision in (R.PROMOTE, R.REPEAT):
        to = request.to_class
        if to is None:
            return "choose the destination class"
        if to.academic_year_id != batch.to_year_id or to.status != "active":
            return f"{to.name} is not an active class in {batch.to_year}"
        wanted = batch.from_class.grade if request.decision == R.REPEAT else next_grade(batch.from_class.grade)
        if wanted is None or to.grade_id != wanted.pk:
            return f"{'repeat' if request.decision == R.REPEAT else 'promote'} goes to {wanted or 'no further grade'}, not {to.grade}"
    if request.decision == R.PROMOTE and next_grade(batch.from_class.grade) is None:
        return "this is the final grade: use complete or leaving"
    return ""


@transaction.atomic
def save_sheet(actor, batch, entries):
    """entries: {learner_id: (decision, to_class or None, note)}. Allowed while draft or returned."""
    batch = PromotionBatch.objects.select_for_update().get(pk=batch.pk)
    if not can_prepare(actor, batch.from_class):
        raise PermissionDenied
    if batch.status not in (PromotionBatch.DRAFT, PromotionBatch.RETURNED):
        raise ValidationError("This sheet has been submitted.")
    rows = {e.learner_id: (e, r) for e, r in sheet_rows(batch)}
    for learner_id, (decision, to_class, note) in entries.items():
        if learner_id not in rows:
            raise ValidationError("The class list changed; reload the page.")
        enrollment, request = rows[learner_id]
        request.decision = decision
        request.to_class = to_class if decision in (R.PROMOTE, R.REPEAT) else None
        request.note = (note or "").strip()
        request.from_enrollment = enrollment
        request.requested_by = actor
        request.status = R.DRAFT
        if request.decision and (request.to_class is not None) == (decision in (R.PROMOTE, R.REPEAT)):
            request.save()
    return batch


@transaction.atomic
def submit_sheet(actor, batch):
    batch = PromotionBatch.objects.select_for_update().get(pk=batch.pk)
    if not can_prepare(actor, batch.from_class):
        raise PermissionDenied
    if batch.status not in (PromotionBatch.DRAFT, PromotionBatch.RETURNED):
        raise ValidationError("This sheet has already been submitted.")
    problems = []
    for enrollment, request in sheet_rows(batch):
        problem = _check_request(batch, request) or ("not saved yet: press Save" if request.pk is None else "")
        if problem:
            problems.append(f"{enrollment.learner.full_name}: {problem}")
    if problems:
        raise ValidationError(problems)
    batch.requests.update(status=R.PENDING)
    batch.status, batch.submitted_at = PromotionBatch.SUBMITTED, timezone.now()
    batch.save(update_fields=["status", "submitted_at", "updated_at"])
    audit.record("promotion_batch.submit", batch, actor=actor, school=batch.school, changes=summary(batch).as_dict())
    return batch


@transaction.atomic
def return_sheet(actor, batch, note):
    """The Headteacher sends the sheet back with a note (decision P5)."""
    if not can_approve(actor):
        raise PermissionDenied
    if not note.strip():
        raise ValidationError("Say what needs changing.")
    batch = PromotionBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != PromotionBatch.SUBMITTED:
        raise ValidationError("Only a submitted sheet can be returned.")
    batch.requests.update(status=R.DRAFT)
    batch.status, batch.decision_note = PromotionBatch.RETURNED, note.strip()
    batch.save(update_fields=["status", "decision_note", "updated_at"])
    audit.record("promotion_batch.return", batch, actor=actor, school=batch.school, reason=note)
    return batch


@dataclass
class Summary:
    destinations: Counter = field(default_factory=Counter)  # (decision, class name) -> count
    total: int = 0

    def as_dict(self):
        return {f"{d} {c}".strip(): n for (d, c), n in sorted(self.destinations.items())}

    def lines(self):
        return [(d, c, n) for (d, c), n in sorted(self.destinations.items())]


def summary(batch):
    s = Summary()
    for request in batch.requests.select_related("to_class"):
        s.destinations[(request.decision, request.to_class.name if request.to_class else "")] += 1
        s.total += 1
    return s


@transaction.atomic
def approve_sheet(actor, batch):
    """Process the whole class in one transaction (design §2 step 5). A class can only be processed once."""
    if not can_approve(actor):
        raise PermissionDenied("Only the Headteacher approves promotions.")
    batch = PromotionBatch.objects.select_for_update().select_related("from_class__grade", "from_year", "to_year").get(pk=batch.pk)
    if batch.status != PromotionBatch.SUBMITTED:
        raise ValidationError("Only a submitted sheet can be approved; this one is " + batch.status + ".")
    requests = list(batch.requests.select_for_update(of=("self",)).select_related("learner", "from_enrollment", "to_class"))
    on_roster = {e.learner_id for e in year_end_roster(batch.from_class)}
    problems = [f"{r.learner.full_name}: {p}" for r in requests if (p := _check_request(batch, r))]
    problems += [f"{e.learner.full_name}: not on the sheet" for e in year_end_roster(batch.from_class)
                 if e.learner_id not in {r.learner_id for r in requests}]
    problems += [f"{r.learner.full_name}: no longer in {batch.from_class.name}" for r in requests if r.learner_id not in on_roster]
    if problems:
        raise ValidationError(problems)
    now, year_end, next_start = timezone.now(), batch.from_year.end_date, batch.to_year.start_date
    for request in requests:
        learner, current = request.learner, Enrollment.objects.select_for_update().get(pk=request.from_enrollment_id)
        if request.decision != R.HOLD:
            current.end_date = min(current.end_date or year_end, year_end)
            current.status, current.end_reason = "ended", END_REASONS[request.decision]
            current.save(update_fields=["end_date", "status", "end_reason", "updated_at"])
        if request.decision in (R.PROMOTE, R.REPEAT):
            if learner.enrollments.exclude(status="reversed").filter(academic_year=batch.to_year).exists():
                raise ValidationError(f"{learner.full_name} is already enrolled in {batch.to_year}.")
            request.resulting_enrollment = Enrollment.objects.create(
                school=learner.school, learner=learner, academic_year=batch.to_year, class_group=request.to_class,
                start_date=next_start, previous_enrollment=current, request=request,
                reason=request.note or f"{request.get_decision_display()} from {batch.from_class.name}",
                created_by=request.requested_by, approved_by=actor, approved_at=now,
            )
        elif request.decision in (R.COMPLETE, R.LEAVING):
            new_status = Learner.COMPLETED if request.decision == R.COMPLETE else Learner.TRANSFERRED
            Learner.objects.filter(pk=learner.pk).update(status=new_status, status_changed_at=now)
        request.status, request.decided_by, request.decided_at = R.APPROVED, actor, now
        request.save(update_fields=["status", "decided_by", "decided_at", "resulting_enrollment", "updated_at"])
    batch.status, batch.decided_by, batch.decided_at = PromotionBatch.APPROVED, actor, now
    batch.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    audit.record("promotion_batch.approve", batch, actor=actor, school=batch.school, changes=summary(batch).as_dict())
    return batch


def held_requests():
    """Approved 'hold' decisions whose learner still has no class in the next year."""
    held = R.objects.filter(kind=R.PROMOTION, decision=R.HOLD, status=R.APPROVED).select_related(
        "learner", "batch__from_class", "batch__to_year")
    return [r for r in held if not r.learner.enrollments.exclude(status="reversed").filter(academic_year=r.batch.to_year).exists()]


@transaction.atomic
def place_held(actor, request, to_class, reason):
    """Decide a held learner individually: the Headteacher places them in a next-year class."""
    if not can_approve(actor):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError("Give a reason for the decision.")
    request = R.objects.select_for_update(of=("self",)).select_related("batch__to_year", "batch__from_year", "batch__from_class__grade", "learner").get(pk=request.pk)
    if request.decision != R.HOLD or request.status != R.APPROVED:
        raise ValidationError("This learner is not held for review.")
    if to_class.academic_year_id != request.batch.to_year_id:
        raise ValidationError(f"Choose a class in {request.batch.to_year}.")
    current = Enrollment.objects.select_for_update().get(pk=request.from_enrollment_id)
    year_end = request.batch.from_year.end_date
    repeat = to_class.grade_id == request.batch.from_class.grade_id
    current.end_date = min(current.end_date or year_end, year_end)
    current.status, current.end_reason = "ended", "repeat" if repeat else "promotion"
    current.save(update_fields=["end_date", "status", "end_reason", "updated_at"])
    new = Enrollment.objects.create(
        school=request.school, learner=request.learner, academic_year=to_class.academic_year, class_group=to_class,
        start_date=to_class.academic_year.start_date, previous_enrollment=current, request=request, reason=reason.strip(),
        created_by=actor, approved_by=actor, approved_at=timezone.now(),
    )
    request.resulting_enrollment, request.decision_note = new, reason.strip()
    request.save(update_fields=["resulting_enrollment", "decision_note", "updated_at"])
    audit.record("promotion.place_held", new, actor=actor, reason=reason,
                 changes={"learner": request.learner.learner_number, "class": to_class.name})
    return new


# Within-year transfers (decision P6) -------------------------------------------------


@transaction.atomic
def request_transfer(actor, learner, to_class, effective_date, reason):
    """Ask the Headteacher to move a learner to another class this year. A Headteacher's own request takes effect at once."""
    if not can_request_transfer(actor, learner):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError("Give a reason for the transfer.")
    current = learner.current_enrollment()
    if current is None:
        raise ValidationError("This learner has no current enrolment to transfer from.")
    if to_class.pk == current.class_group_id:
        raise ValidationError("The learner is already in that class.")
    if to_class.academic_year_id != current.academic_year_id:
        raise ValidationError("Transfers stay within the academic year; moving to the next year is a promotion.")
    if effective_date <= current.start_date:
        raise ValidationError("The transfer date must be after the current enrolment started.")
    if R.objects.filter(learner=learner, kind=R.TRANSFER, status=R.PENDING).exists():
        raise ValidationError("A transfer request for this learner is already waiting for the Headteacher.")
    request = R.objects.create(school=learner.school, kind=R.TRANSFER, learner=learner, from_enrollment=current,
                               to_class=to_class, effective_date=effective_date, note=reason.strip(),
                               status=R.PENDING, requested_by=actor)
    audit.record("transfer_request.create", request, actor=actor, school=learner.school, reason=reason,
                 changes={"learner": learner.learner_number, "to": to_class.name, "effective": effective_date})
    if can_approve(actor):
        approve_transfer(actor, request, "Requested by the Headteacher")
    return request


@transaction.atomic
def approve_transfer(actor, request, note=""):
    if not can_approve(actor):
        raise PermissionDenied("Only the Headteacher approves transfers.")
    request = R.objects.select_for_update(of=("self",)).select_related("learner", "to_class").get(pk=request.pk)
    if request.kind != R.TRANSFER or request.status != R.PENDING:
        raise ValidationError("Only a pending transfer request can be approved.")
    new = services.transfer(request.learner, request.to_class, request.effective_date, request.note, actor)
    new.request = request
    new.save(update_fields=["request", "updated_at"])
    request.status, request.decided_by, request.decided_at = R.APPROVED, actor, timezone.now()
    request.decision_note, request.resulting_enrollment = note.strip(), new
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "resulting_enrollment", "updated_at"])
    audit.record("transfer_request.approve", request, actor=actor, school=request.school, reason=note)
    return request


@transaction.atomic
def reject_transfer(actor, request, note):
    if not can_approve(actor):
        raise PermissionDenied
    if not note.strip():
        raise ValidationError("Say why the transfer is rejected.")
    request = R.objects.select_for_update().get(pk=request.pk)
    if request.status != R.PENDING:
        raise ValidationError("Only a pending request can be rejected.")
    request.status, request.decided_by, request.decided_at, request.decision_note = R.REJECTED, actor, timezone.now(), note.strip()
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
    audit.record("transfer_request.reject", request, actor=actor, school=request.school, reason=note)
    return request


@transaction.atomic
def cancel_transfer(actor, request):
    request = R.objects.select_for_update().get(pk=request.pk)
    if request.status != R.PENDING or not (request.requested_by_id == actor.pk or can_approve(actor)):
        raise PermissionDenied
    request.status = R.CANCELLED
    request.save(update_fields=["status", "updated_at"])
    audit.record("transfer_request.cancel", request, actor=actor, school=request.school)
    return request


def pending_counts(user):
    """For the dashboard."""
    if not can_approve(user):
        return {}
    return {
        "sheets": PromotionBatch.objects.filter(status=PromotionBatch.SUBMITTED).count(),
        "transfers": R.objects.filter(kind=R.TRANSFER, status=R.PENDING).count(),
    }

