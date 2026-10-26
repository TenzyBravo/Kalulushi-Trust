"""Learner, guardian and enrollment changes. Each runs in one transaction with its audit record."""
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from core import audit
from core.models import School
from core.phone import normalise_phone

from . import numbers
from .duplicates import find_likely_duplicates
from .models import Enrollment, Guardian, GuardianContact, Learner, LearnerGuardian, LearnerSupportNote

LEARNER_FIELDS = ["first_name", "last_name", "other_names", "date_of_birth", "gender", "previous_school"]


class DuplicateWarning(Exception):
    def __init__(self, matches):
        self.matches = matches
        super().__init__(f"{len(matches)} likely duplicate(s)")


@dataclass
class GuardianInput:
    full_name: str
    phone: str
    relationship: str = ""
    email: str = ""
    existing: Guardian | None = None  # staff chose to link an existing guardian (D3)


@dataclass
class Registration:
    first_name: str
    last_name: str
    class_group: object
    start_date: object
    guardian: GuardianInput
    other_names: str = ""
    date_of_birth: object = None
    gender: str = ""
    previous_school: str = ""
    support_notes: list = field(default_factory=list)  # [(category, note)]


def _check_enrollment_dates(class_group, start_date):
    year = class_group.academic_year
    if not year.contains(start_date):
        raise ValidationError(f"The enrolment date must fall inside academic year {year} ({year.start_date} to {year.end_date}).")
    if class_group.status != "active":
        raise ValidationError(f"Class {class_group.name} is closed.")


@transaction.atomic
def register_learner(reg: Registration, actor, *, override_reason="", school=None, check_duplicates=True):
    """Create the learner, their guardian link and first enrollment, with a new permanent number."""
    school = school or School.current()
    _check_enrollment_dates(reg.class_group, reg.start_date)
    phone = normalise_phone(reg.guardian.phone)

    if check_duplicates:
        matches = find_likely_duplicates(school, reg.first_name, reg.last_name, reg.date_of_birth, phone)
        if matches:
            if not override_reason.strip():
                raise DuplicateWarning(matches)
            if not actor.has_perm("learners.override_duplicate"):
                raise PermissionDenied("You may not save a learner that looks like a duplicate (D5).")

    learner = Learner.objects.create(
        school=school,
        learner_number=numbers.next_number(school),
        first_name=reg.first_name.strip(),
        last_name=reg.last_name.strip(),
        other_names=reg.other_names.strip(),
        date_of_birth=reg.date_of_birth,
        gender=reg.gender,
        registration_date=reg.start_date,
        previous_school=reg.previous_school.strip(),
        created_by=actor,
    )
    audit.record("learner.create", learner, actor=actor,
                 changes={f: [None, getattr(learner, f)] for f in LEARNER_FIELDS + ["learner_number"] if getattr(learner, f)})
    if check_duplicates and matches:
        audit.record("duplicate.override", learner, actor=actor, reason=override_reason.strip(),
                     changes={"matched_learners": [None, [m.learner_number for m, _ in matches]]})

    guardian = reg.guardian.existing or create_guardian(school, reg.guardian.full_name, phone, reg.guardian.email, actor)
    link_guardian(learner, guardian, actor, relationship=reg.guardian.relationship, contact_role=LearnerGuardian.PRIMARY)

    enrollment = Enrollment.objects.create(
        school=school, learner=learner, academic_year=reg.class_group.academic_year, class_group=reg.class_group,
        start_date=reg.start_date, created_by=actor, reason="New registration",
    )
    audit.record("enrollment.create", enrollment, actor=actor,
                 changes={"class": [None, reg.class_group.name], "start_date": [None, reg.start_date]})

    for category, note in reg.support_notes:
        add_support_note(learner, category, note, actor)
    return learner


@transaction.atomic
def update_learner(learner, data, actor):
    learner = Learner.objects.select_for_update().get(pk=learner.pk)
    before = audit.snapshot(learner, LEARNER_FIELDS)
    for f in LEARNER_FIELDS:
        if f in data:
            value = data[f]
            setattr(learner, f, value if value is not None or f == "date_of_birth" else "")
    changes = audit.diff(before, audit.snapshot(learner, LEARNER_FIELDS))
    if changes:
        learner.save()
        audit.record("learner.update", learner, changes=changes, actor=actor)
    return learner


@transaction.atomic
def change_status(learner, status, reason, actor):
    if status not in Learner.STATUSES:
        raise ValidationError("Unknown status.")
    if not reason.strip():
        raise ValidationError("A reason is required to change a learner's status.")
    learner = Learner.objects.select_for_update().get(pk=learner.pk)
    old = learner.status
    if old == status:
        return learner
    learner.status, learner.status_changed_at = status, timezone.now()
    learner.save(update_fields=["status", "status_changed_at", "updated_at"])
    audit.record("learner.status_change", learner, changes={"status": [old, status]}, reason=reason, actor=actor)
    return learner


# Guardians and contacts ----------------------------------------------------------------------

@transaction.atomic
def create_guardian(school, full_name, phone, email, actor):
    guardian = Guardian.objects.create(school=school, full_name=full_name.strip(), email=(email or "").strip())
    contact = GuardianContact.objects.create(guardian=guardian, phone=normalise_phone(phone))
    audit.record("guardian.create", guardian, actor=actor,
                 changes={"full_name": [None, guardian.full_name], "phone": [None, contact.phone]})
    return guardian


@transaction.atomic
def link_guardian(learner, guardian, actor, relationship="", contact_role=LearnerGuardian.NONE, receives_results=True):
    if learner.guardian_links.filter(guardian=guardian, status="active").exists():
        raise ValidationError(f"{guardian} is already linked to this learner.")
    if contact_role != LearnerGuardian.NONE:
        _clear_role(learner, contact_role, actor)
    link = LearnerGuardian.objects.create(
        learner=learner, guardian=guardian, relationship=relationship.strip(),
        contact_role=contact_role, receives_results=receives_results,
    )
    audit.record("guardian_link.create", link, school=learner.school, actor=actor,
                 changes={"guardian": [None, guardian.full_name], "contact_role": [None, contact_role],
                          "relationship": [None, link.relationship]})
    return link


def _clear_role(learner, role, actor):
    """Demote the current holder first: a partial unique index can't be deferred (design §6)."""
    for old in learner.guardian_links.select_for_update().filter(status="active", contact_role=role):
        old.contact_role = LearnerGuardian.NONE
        old.save(update_fields=["contact_role"])
        audit.record("guardian_link.update", old, school=learner.school, actor=actor,
                     changes={"contact_role": [role, LearnerGuardian.NONE]})


@transaction.atomic
def set_contact_role(link, role, actor):
    link = LearnerGuardian.objects.select_for_update().get(pk=link.pk)
    if link.status != "active" or link.contact_role == role:
        return link
    old = link.contact_role
    if role != LearnerGuardian.NONE:
        _clear_role(link.learner, role, actor)
    link.contact_role = role
    link.save(update_fields=["contact_role"])
    audit.record("guardian_link.update", link, school=link.learner.school, actor=actor,
                 changes={"contact_role": [old, role]})
    return link


@transaction.atomic
def set_receives_results(link, value, actor):
    link = LearnerGuardian.objects.select_for_update().get(pk=link.pk)
    if link.receives_results != value:
        audit.record("guardian_link.update", link, school=link.learner.school, actor=actor,
                     changes={"receives_results": [link.receives_results, value]})
        link.receives_results = value
        link.save(update_fields=["receives_results"])
    return link


@transaction.atomic
def remove_link(link, reason, actor):
    if not reason.strip():
        raise ValidationError("A reason is required to remove a guardian.")
    link = LearnerGuardian.objects.select_for_update().get(pk=link.pk)
    old_role = link.contact_role
    link.status, link.contact_role = "removed", LearnerGuardian.NONE
    link.removed_at, link.removed_by, link.removal_reason = timezone.now(), actor, reason.strip()
    link.save()
    audit.record("guardian_link.remove", link, school=link.learner.school, actor=actor, reason=reason,
                 changes={"status": ["active", "removed"], "contact_role": [old_role, LearnerGuardian.NONE]})
    return link


@transaction.atomic
def change_phone(guardian, new_phone, actor, reason=""):
    """Retire the current number and add the new one unverified, so it must be re-verified."""
    phone = normalise_phone(new_phone)
    current = guardian.contacts.select_for_update().filter(is_current=True).first()
    if current and current.phone == phone:
        return current
    if current:
        current.is_current, current.retired_at = False, timezone.now()
        current.save(update_fields=["is_current", "retired_at"])
    contact = GuardianContact.objects.create(guardian=guardian, phone=phone)
    audit.record("guardian_contact.change", guardian, actor=actor, reason=reason,
                 changes={"phone": [current.phone if current else None, phone]})
    return contact


@transaction.atomic
def verify_contact(contact, method, actor):
    if method not in ("in_person", "phone_call", "other"):
        raise ValidationError("SMS-code verification arrives with the SMS module; choose another method.")
    contact = GuardianContact.objects.select_for_update().get(pk=contact.pk)
    if not contact.is_current:
        raise ValidationError("Only the current number can be verified.")
    contact.verified_at, contact.verified_by, contact.verification_method = timezone.now(), actor, method
    contact.save(update_fields=["verified_at", "verified_by", "verification_method"])
    audit.record("guardian_contact.verify", contact, school=contact.guardian.school, actor=actor,
                 changes={"verified": [False, True], "method": [None, method]})
    return contact


@transaction.atomic
def update_guardian(guardian, full_name, email, actor):
    before = audit.snapshot(guardian, ["full_name", "email"])
    guardian.full_name, guardian.email = full_name.strip(), (email or "").strip()
    changes = audit.diff(before, audit.snapshot(guardian, ["full_name", "email"]))
    if changes:
        guardian.save()
        audit.record("guardian.update", guardian, changes=changes, actor=actor)
    return guardian


# Support notes ------------------------------------------------------------------------------

@transaction.atomic
def add_support_note(learner, category, note, actor):
    item = LearnerSupportNote.objects.create(learner=learner, category=category, note=note.strip(), created_by=actor)
    # The note text stays out of the audit log; only that one was added.
    audit.record("support_note.create", item, school=learner.school, actor=actor, changes={"category": [None, category]})
    return item


@transaction.atomic
def view_support_notes(learner, actor):
    """Reading notes is itself audited (support_note.view)."""
    notes = list(learner.support_notes.filter(archived_at__isnull=True))
    if notes:
        audit.record("support_note.view", learner, actor=actor, changes={"notes": [None, [n.pk for n in notes]]})
    return notes


@transaction.atomic
def archive_support_note(note, actor, reason=""):
    note.archived_at, note.archived_by = timezone.now(), actor
    note.save(update_fields=["archived_at", "archived_by"])
    audit.record("support_note.archive", note, school=note.learner.school, actor=actor, reason=reason)
    return note


# Enrollments --------------------------------------------------------------------------------

@transaction.atomic
def transfer(learner, to_class, effective_date, reason, actor):
    """End the current enrollment the day before and start the new one (design §6, Transfer)."""
    if not actor.has_perm("learners.transfer_learner"):
        raise PermissionDenied("Only an approver may transfer a learner.")
    if not reason.strip():
        raise ValidationError("A reason is required for a transfer.")
    current = learner.enrollments.select_for_update().filter(status="active", end_date__isnull=True).first()
    if current is None:
        raise ValidationError("This learner has no current enrolment to transfer from.")
    if to_class.pk == current.class_group_id:
        raise ValidationError("The learner is already in that class.")
    if to_class.academic_year_id != current.academic_year_id:
        raise ValidationError("Transfers stay within the academic year; moving to the next year is a promotion.")
    if effective_date <= current.start_date:
        raise ValidationError("The transfer date must be after the current enrolment started.")
    _check_enrollment_dates(to_class, effective_date)

    current.end_date = effective_date - timedelta(days=1)
    current.status, current.end_reason = "ended", "transfer"
    current.save(update_fields=["end_date", "status", "end_reason", "updated_at"])
    new = Enrollment.objects.create(
        school=learner.school, learner=learner, academic_year=to_class.academic_year, class_group=to_class,
        start_date=effective_date, previous_enrollment=current, reason=reason.strip(),
        created_by=actor, approved_by=actor, approved_at=timezone.now(),
    )
    audit.record("enrollment.transfer", new, actor=actor, reason=reason,
                 changes={"class": [current.class_group.name, to_class.name], "effective_date": [None, effective_date]})
    return new


@transaction.atomic
def reverse_enrollment(enrollment, reason, actor):
    """Mark a mistaken enrollment reversed; the row stays for history (design §6, Correction)."""
    if not reason.strip():
        raise ValidationError("A reason is required to reverse an enrolment.")
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    if enrollment.status == "reversed":
        return enrollment
    old = enrollment.status
    enrollment.status = "reversed"
    enrollment.reason = (enrollment.reason + "\n" if enrollment.reason else "") + f"Reversed: {reason.strip()}"
    enrollment.save(update_fields=["status", "reason", "updated_at"])
    audit.record("enrollment.reverse", enrollment, actor=actor, reason=reason, changes={"status": [old, "reversed"]})
    return enrollment


def _check_no_overlap(learner, start_date):
    """Friendly message first; the exclusion constraint is the real guarantee."""
    clash = (
        learner.enrollments.exclude(status="reversed")
        .filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=start_date))
        .first()
    )
    if clash:
        raise ValidationError(f"This learner is already enrolled in {clash.class_group.name} on that date.")


@transaction.atomic
def enroll(learner, class_group, start_date, reason, actor):
    """A new enrollment, e.g. after correcting a reversed one."""
    _check_enrollment_dates(class_group, start_date)
    _check_no_overlap(learner, start_date)
    enrollment = Enrollment.objects.create(
        school=learner.school, learner=learner, academic_year=class_group.academic_year, class_group=class_group,
        start_date=start_date, reason=reason.strip(), created_by=actor,
    )
    audit.record("enrollment.create", enrollment, actor=actor, reason=reason,
                 changes={"class": [None, class_group.name], "start_date": [None, start_date]})
    return enrollment
