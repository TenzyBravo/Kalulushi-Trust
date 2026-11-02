"""Learner, guardian and enrolment changes (design §6).

Each public function runs in one transaction and writes its audit record in
that same transaction. Views and the importer call these; nothing else writes
these tables.
"""
import datetime
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import access
from audit import log
from core.phone import normalise_phone

from . import duplicates, numbers
from .models import Enrollment, Guardian, GuardianContact, Learner, LearnerGuardian, LearnerSupportNote

LEARNER_FIELDS = [
    "first_name", "last_name", "other_names", "date_of_birth", "gender",
    "registration_date", "previous_school", "status",
]
EDITABLE_LEARNER_FIELDS = [f for f in LEARNER_FIELDS if f not in ("status", "registration_date")]

# Learner status -> why their enrolment ends.
LEAVING_REASONS = {"transferred": "transfer", "withdrawn": "withdrawal", "completed": "completion"}


class LikelyDuplicate(Exception):
    """Raised instead of saving when likely duplicates exist and no override reason was given."""

    def __init__(self, matches):
        self.matches = matches  # [(learner, reason), ...]
        super().__init__(f"{len(matches)} likely duplicate(s) found")


@dataclass
class GuardianInput:
    """One guardian for a new registration: an existing guardian, or the details of a new one."""

    relationship: str
    contact_role: str = "none"
    receives_results: bool = True
    existing: Guardian | None = None
    full_name: str = ""
    phone: str = ""
    email: str = ""

    def current_phone(self):
        if self.existing:
            contact = self.existing.current_contact()
            return contact.phone if contact else None
        return normalise_phone(self.phone) if self.phone else None


def _require(actor, perm):
    if not actor.has_perm(perm):
        raise PermissionDenied


# Guardians ----------------------------------------------------------------


def guardians_with_phone(school, phone):
    """Existing guardians whose current number is phone. Registration suggests these;
    staff confirm before linking, because one handset may serve two families (decision D3)."""
    return Guardian.objects.filter(
        school=school, contacts__phone=normalise_phone(phone), contacts__is_current=True
    ).distinct()


def _create_guardian(actor, school, full_name, phone, email=""):
    if not full_name.strip():
        raise ValidationError("Enter the guardian's name.")
    guardian = Guardian(school=school, full_name=full_name.strip(), email=email.strip())
    guardian.full_clean()
    guardian.save()
    contact = GuardianContact.objects.create(guardian=guardian, phone=normalise_phone(phone))
    log.record(
        "guardian.create",
        guardian,
        changes={"full_name": guardian.full_name, "email": guardian.email, "phone": contact.phone},
        actor=actor,
    )
    return guardian


def _link(actor, learner, guardian, relationship, contact_role, receives_results):
    link = LearnerGuardian(
        learner=learner,
        guardian=guardian,
        relationship=relationship.strip(),
        contact_role=contact_role,
        receives_results=receives_results,
    )
    link.full_clean()
    link.save()
    log.record(
        "learner_guardian.link",
        learner,
        changes={
            "guardian": guardian.pk,
            "guardian_name": guardian.full_name,
            "relationship": link.relationship,
            "contact_role": contact_role,
            "receives_results": receives_results,
        },
        actor=actor,
    )
    return link


@transaction.atomic
def add_guardian(actor, learner, data: GuardianInput):
    _require(actor, "learners.change_learner")
    guardian = data.existing or _create_guardian(actor, learner.school, data.full_name, data.phone, data.email)
    if data.contact_role in ("primary", "alternative"):
        holder = learner.guardian_links.filter(status="active", contact_role=data.contact_role).first()
        if holder:
            raise ValidationError(
                f"{holder.guardian} is already the {holder.get_contact_role_display().lower()}. "
                "Add this guardian first, then change contact roles."
            )
    return _link(actor, learner, guardian, data.relationship, data.contact_role, data.receives_results)


@transaction.atomic
def update_guardian(actor, guardian, *, full_name, email):
    _require(actor, "learners.change_guardian")
    before = log.snapshot(guardian, ["full_name", "email"])
    guardian.full_name = full_name.strip()
    guardian.email = email.strip()
    guardian.full_clean()
    guardian.save()
    changes = log.diff(before, log.snapshot(guardian, ["full_name", "email"]))
    if changes:
        log.record("guardian.update", guardian, changes=changes, actor=actor)
    return guardian


@transaction.atomic
def update_link(actor, link, *, relationship, receives_results):
    _require(actor, "learners.change_learnerguardian")
    before = log.snapshot(link, ["relationship", "receives_results"])
    link.relationship = relationship.strip()
    link.receives_results = receives_results
    link.save(update_fields=["relationship", "receives_results"])
    changes = log.diff(before, log.snapshot(link, ["relationship", "receives_results"]))
    if changes:
        log.record("learner_guardian.update", link.learner, changes={"guardian": link.guardian_id, **changes}, actor=actor)
    return link


@transaction.atomic
def set_contact_role(actor, link, role):
    """Change a guardian's contact role for one learner.

    The one-primary and one-alternative rules are partial unique indexes, which
    PostgreSQL cannot defer, so the holder of a role is moved out before the new
    holder moves in (design §6). Making the alternative primary swaps the two.
    """
    _require(actor, "learners.change_learnerguardian")
    link = LearnerGuardian.objects.select_for_update().get(pk=link.pk)
    if link.status != "active":
        raise ValidationError("This guardian is no longer linked to the learner.")
    old_role = link.contact_role
    if role == old_role:
        return link
    siblings = LearnerGuardian.objects.select_for_update().filter(learner=link.learner, status="active").exclude(pk=link.pk)
    holder = siblings.filter(contact_role=role).first() if role != "none" else None
    if old_role == "primary" and holder is None:
        raise ValidationError("Make another guardian the primary contact instead; a learner always keeps one.")

    link.contact_role = "none"
    link.save(update_fields=["contact_role"])
    if holder:
        # A swap when the two trade primary and alternative; otherwise the old holder steps down.
        holder.contact_role = old_role if old_role in ("primary", "alternative") else "none"
        holder.save(update_fields=["contact_role"])
    link.contact_role = role
    link.save(update_fields=["contact_role"])

    changes = {"guardian": link.guardian_id, "contact_role": [old_role, role]}
    if holder:
        changes["previous_holder"] = holder.guardian_id
        changes["previous_holder_now"] = holder.contact_role
    log.record("learner_guardian.contact_role", link.learner, changes=changes, actor=actor)
    return link


@transaction.atomic
def remove_link(actor, link, reason):
    _require(actor, "learners.change_learnerguardian")
    if not reason.strip():
        raise ValidationError("Give a reason for removing the guardian.")
    if link.contact_role == "primary":
        raise ValidationError("Make another guardian the primary contact before removing this one.")
    link.status = "removed"
    link.removed_at = timezone.now()
    link.removed_by = actor
    link.removal_reason = reason.strip()
    link.save(update_fields=["status", "removed_at", "removed_by", "removal_reason"])
    log.record("learner_guardian.remove", link.learner, changes={"guardian": link.guardian_id}, reason=reason, actor=actor)
    return link


@transaction.atomic
def change_phone(actor, guardian, phone):
    """Retire the current number and add the new one, unverified (baseline §10)."""
    _require(actor, "learners.change_guardiancontact")
    phone = normalise_phone(phone)
    current = GuardianContact.objects.select_for_update().filter(guardian=guardian, is_current=True).first()
    if current and current.phone == phone:
        return current
    if current:
        current.is_current = False
        current.retired_at = timezone.now()
        current.save(update_fields=["is_current", "retired_at"])
    contact = GuardianContact.objects.create(guardian=guardian, phone=phone)
    log.record(
        "guardian_contact.change",
        guardian,
        changes={"phone": [current.phone if current else None, phone]},
        actor=actor,
    )
    return contact


@transaction.atomic
def verify_contact(actor, contact, method):
    _require(actor, "learners.change_guardiancontact")
    if not contact.is_current:
        raise ValidationError("Only the current number can be verified.")
    if method not in dict(GuardianContact.METHODS):
        raise ValidationError("Choose how the number was verified.")
    contact.verified_at = timezone.now()
    contact.verified_by = actor
    contact.verification_method = method
    contact.save(update_fields=["verified_at", "verified_by", "verification_method"])
    log.record("guardian_contact.verify", contact.guardian, changes={"phone": contact.phone, "method": method}, actor=actor)
    return contact


# Enrolments ----------------------------------------------------------------


def _save_enrollment(enrollment):
    """Validate and save. The exclusion constraint is the real guarantee (design §6): if a
    concurrent request slips past validation, the database still refuses the overlap."""
    enrollment.full_clean()
    try:
        with transaction.atomic():
            enrollment.save()
    except IntegrityError as exc:
        if "enrollments_no_overlap" in str(exc):
            raise ValidationError("This learner already has an enrolment covering these dates.") from exc
        raise
    return enrollment


def _check_class(school_class, on_date):
    year = school_class.academic_year
    if school_class.status != "active":
        raise ValidationError(f"{school_class.name} is closed.")
    if year.status == "closed":
        raise ValidationError(f"{year} is closed.")
    if not (year.start_date <= on_date <= year.end_date):
        raise ValidationError(f"{on_date:%d %b %Y} is outside the {year} academic year.")


def _enroll(actor, learner, school_class, start_date, reason="", previous=None):
    _check_class(school_class, start_date)
    enrollment = Enrollment(
        school=learner.school,
        learner=learner,
        academic_year=school_class.academic_year,
        school_class=school_class,
        start_date=start_date,
        previous_enrollment=previous,
        reason=reason,
        created_by=actor,
    )
    _save_enrollment(enrollment)
    log.record(
        "enrollment.create",
        enrollment,
        changes={"learner": learner.pk, "class": school_class.name, "year": school_class.academic_year.name, "start_date": start_date},
        reason=reason,
        actor=actor,
    )
    return enrollment


@transaction.atomic
def enroll(actor, learner, school_class, start_date, reason=""):
    _require(actor, "learners.add_enrollment")
    previous = learner.enrollments.exclude(status="reversed").order_by("-start_date").first()
    return _enroll(actor, learner, school_class, start_date, reason, previous)


def _end(actor, enrollment, end_date, end_reason, reason=""):
    if enrollment.status != "active":
        raise ValidationError("Only an active enrolment can be ended.")
    if end_date < enrollment.start_date:
        raise ValidationError("The enrolment cannot end before it started.")
    enrollment.end_date = end_date
    enrollment.status = "ended"
    enrollment.end_reason = end_reason
    enrollment.save(update_fields=["end_date", "status", "end_reason", "updated_at"])
    log.record(
        "enrollment.end",
        enrollment,
        changes={"learner": enrollment.learner_id, "end_date": end_date, "end_reason": end_reason},
        reason=reason,
        actor=actor,
    )


@transaction.atomic
def transfer(actor, enrollment, new_class, effective_date, reason=""):
    """Move a learner to another class in the same year from effective_date (design §6).
    Earlier records stay with the old enrolment."""
    _require(actor, "learners.change_enrollment")
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    if new_class.pk == enrollment.school_class_id:
        raise ValidationError("The learner is already in this class.")
    if new_class.academic_year_id != enrollment.academic_year_id:
        raise ValidationError("A transfer stays within the academic year. Year-end moves come with promotion.")
    if effective_date <= enrollment.start_date:
        raise ValidationError(
            "The transfer must take effect after the current enrolment started. "
            "If the learner was put in the wrong class, correct the enrolment instead."
        )
    _end(actor, enrollment, effective_date - datetime.timedelta(days=1), "transfer", reason)
    return _enroll(actor, enrollment.learner, new_class, effective_date, reason, previous=enrollment)


@transaction.atomic
def correct(actor, enrollment, right_class, reason):
    """Fix a mistaken enrolment: mark it reversed and enrol the learner in the right class
    from the same date. The reversed row stays for history (design §6)."""
    _require(actor, "learners.change_enrollment")
    if not reason.strip():
        raise ValidationError("Give a reason for the correction.")
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    if enrollment.status != "active":
        raise ValidationError("Only an active enrolment can be corrected.")
    enrollment.status = "reversed"
    enrollment.save(update_fields=["status", "updated_at"])
    log.record(
        "enrollment.reverse",
        enrollment,
        changes={"learner": enrollment.learner_id, "class": enrollment.school_class.name},
        reason=reason,
        actor=actor,
    )
    return _enroll(
        actor, enrollment.learner, right_class, enrollment.start_date, reason, previous=enrollment.previous_enrollment
    )


# Learners ------------------------------------------------------------------


@transaction.atomic
def register(actor, *, school_class, guardians, start_date=None, override_reason="", **fields):
    """Register a learner, link their guardians and enrol them, all or nothing.

    Raises LikelyDuplicate if similar learners exist and no override_reason is
    given. Saving anyway needs the override_duplicate permission (decision D5).
    """
    _require(actor, "learners.add_learner")
    school = school_class.school
    primaries = [g for g in guardians if g.contact_role == "primary"]
    if len(primaries) != 1:
        raise ValidationError("Choose exactly one primary contact.")
    if len([g for g in guardians if g.contact_role == "alternative"]) > 1:
        raise ValidationError("Choose at most one alternative contact.")

    fields.setdefault("registration_date", timezone.localdate())
    learner = Learner(school=school, created_by=actor, learner_number="pending", **fields)
    learner.full_clean(exclude=["learner_number"])
    start_date = start_date or max(learner.registration_date, school_class.academic_year.start_date)
    _check_class(school_class, start_date)

    matches = duplicates.find(
        school,
        learner.first_name,
        learner.last_name,
        learner.date_of_birth,
        phones=[p for p in (g.current_phone() for g in guardians) if p],
    )
    if matches:
        if not override_reason.strip():
            raise LikelyDuplicate(matches)
        if not actor.has_perm("learners.override_duplicate"):
            raise PermissionDenied("You may not save a learner who looks like a duplicate. Ask the Headteacher.")

    learner.learner_number = numbers.next_number(school)
    learner.save()
    log.record("learner.create", learner, changes={"learner_number": learner.learner_number, **log.snapshot(learner, LEARNER_FIELDS)}, actor=actor)
    if matches:
        log.record(
            "duplicate.override",
            learner,
            changes={"matches": [m.learner_number for m, _ in matches], "why_flagged": [r for _, r in matches]},
            reason=override_reason,
            actor=actor,
        )

    for g in guardians:
        guardian = g.existing or _create_guardian(actor, school, g.full_name, g.phone, g.email)
        _link(actor, learner, guardian, g.relationship, g.contact_role, g.receives_results)

    _enroll(actor, learner, school_class, start_date, previous=None)
    return learner


@transaction.atomic
def update(actor, learner, **fields):
    _require(actor, "learners.change_learner")
    unknown = set(fields) - set(EDITABLE_LEARNER_FIELDS)
    if unknown:
        raise ValueError(f"Not editable here: {sorted(unknown)}")
    before = log.snapshot(learner, LEARNER_FIELDS)
    for name, value in fields.items():
        setattr(learner, name, value)
    learner.full_clean()
    learner.save()
    changes = log.diff(before, log.snapshot(learner, LEARNER_FIELDS))
    if changes:
        log.record("learner.update", learner, changes=changes, actor=actor)
    return learner


@transaction.atomic
def change_status(actor, learner, status, reason, effective_date=None):
    """Record a learner leaving (or returning). Leaving ends the active enrolment the day before."""
    _require(actor, "learners.change_learner")
    if status not in dict(Learner.STATUSES):
        raise ValidationError("Unknown status.")
    if not reason.strip():
        raise ValidationError("Give a reason for the status change.")
    learner = Learner.objects.select_for_update().get(pk=learner.pk)
    old = learner.status
    if status == old:
        return learner
    if status in LEAVING_REASONS:
        effective_date = effective_date or timezone.localdate()
        for enrollment in learner.enrollments.filter(status="active"):
            end = max(enrollment.start_date, effective_date - datetime.timedelta(days=1))
            _end(actor, enrollment, end, LEAVING_REASONS[status], reason)
    learner.status = status
    learner.status_changed_at = timezone.now()
    learner.save(update_fields=["status", "status_changed_at", "updated_at"])
    log.record("learner.status", learner, changes={"status": [old, status]}, reason=reason, actor=actor)
    return learner


# Support notes -------------------------------------------------------------


@transaction.atomic
def add_note(actor, learner, category, note):
    _require(actor, "learners.add_learnersupportnote")
    obj = LearnerSupportNote(learner=learner, category=category, note=note.strip(), created_by=actor)
    obj.full_clean()
    obj.save()
    log.record("support_note.create", obj, changes={"learner": learner.pk, "category": category}, actor=actor, school_id=learner.school_id)
    return obj


@transaction.atomic
def read_notes(actor, learner, include_archived=False):
    """Return the learner's support notes, writing a support_note.view record for each."""
    if not access.can_view_support_notes(actor, learner):
        raise PermissionDenied
    notes = list(learner.support_notes.all() if include_archived else learner.support_notes.filter(archived_at__isnull=True))
    for note in notes:
        log.record("support_note.view", note, changes={"learner": learner.pk}, actor=actor, school_id=learner.school_id)
    return notes


@transaction.atomic
def archive_note(actor, note):
    _require(actor, "learners.change_learnersupportnote")
    if note.archived_at:
        return note
    note.archived_at = timezone.now()
    note.archived_by = actor
    note.save(update_fields=["archived_at", "archived_by", "updated_at"])
    log.record("support_note.archive", note, changes={"learner": note.learner_id}, actor=actor, school_id=note.learner.school_id)
    return note
