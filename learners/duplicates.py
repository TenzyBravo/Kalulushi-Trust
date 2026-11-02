"""Likely-duplicate checks for individual and bulk registration (design §6).

A match is a warning, not a block.
"""
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q, TextField, Value
from django.db.models.functions import Concat, Lower

from .models import Guardian, GuardianContact, Learner

THRESHOLD = 0.6


def _full_name():
    return Lower(Concat("first_name", Value(" "), "last_name", output_field=TextField()))


def find_likely_duplicates(school, first_name, last_name, date_of_birth=None, guardian_phone=None, exclude_pk=None):
    """Existing learners with the same birth date and a similar name, or a guardian phone and similar first name."""
    name = f"{first_name} {last_name}".strip().lower()
    base = Learner.objects.filter(school=school)
    if exclude_pk:
        base = base.exclude(pk=exclude_pk)
    found = {}
    if date_of_birth:
        by_dob = (
            base.filter(date_of_birth=date_of_birth)
            .annotate(similarity=TrigramSimilarity(_full_name(), name))
            .filter(similarity__gte=THRESHOLD)
        )
        for learner in by_dob:
            found[learner.pk] = (learner, "same date of birth and a similar name")
    if guardian_phone:
        learner_ids = GuardianContact.objects.filter(phone=guardian_phone, is_current=True).values_list(
            "guardian__learner_links__learner_id", flat=True
        )
        by_phone = (
            base.filter(pk__in=[i for i in learner_ids if i])
            .annotate(similarity=TrigramSimilarity(Lower("first_name"), first_name.strip().lower()))
            .filter(similarity__gte=THRESHOLD)
        )
        for learner in by_phone:
            found.setdefault(learner.pk, (learner, "a guardian with the same phone and a similar first name"))
    return list(found.values())


def guardians_with_phone(school, phone):
    """Existing guardians using this number: suggested for linking, never merged automatically (D3)."""
    return (
        Guardian.objects.filter(school=school, contacts__phone=phone, contacts__is_current=True)
        .filter(~Q(status="inactive"))
        .distinct()
    )
