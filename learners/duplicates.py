"""Likely-duplicate checks before registering a learner (design §6, baseline §4).

A learner in the same school counts as a likely duplicate when either:
  - the date of birth matches and the full name is at least 60% similar, or
  - a linked guardian has the same current phone number and the first name
    is at least 60% similar.
A match is a warning, never a block.
"""
from django.db import connection

from .models import Learner

SIMILARITY = 0.6

_SQL = """
SELECT l.id, 'same date of birth and similar name'
  FROM learners l
 WHERE l.school_id = %(school)s
   AND %(dob)s::date IS NOT NULL
   AND l.date_of_birth = %(dob)s::date
   AND similarity(lower(l.first_name || ' ' || l.last_name), lower(%(full_name)s)) >= %(threshold)s
UNION
SELECT l.id, 'guardian phone ' || gc.phone || ' and similar first name'
  FROM learners l
  JOIN learner_guardians lg ON lg.learner_id = l.id AND lg.status = 'active'
  JOIN guardian_contacts gc ON gc.guardian_id = lg.guardian_id AND gc.is_current
 WHERE l.school_id = %(school)s
   AND gc.phone = ANY(%(phones)s)
   AND similarity(lower(l.first_name), lower(%(first_name)s)) >= %(threshold)s
"""


def find(school, first_name, last_name, date_of_birth=None, phones=(), exclude_ids=()):
    """Return [(learner, reason), ...] for likely duplicates, oldest learner first."""
    with connection.cursor() as cursor:
        cursor.execute(
            _SQL,
            {
                "school": school.pk,
                "dob": date_of_birth,
                "full_name": f"{first_name} {last_name}",
                "first_name": first_name,
                "phones": list(phones),
                "threshold": SIMILARITY,
            },
        )
        reasons = {}
        for learner_id, reason in cursor.fetchall():
            if learner_id not in exclude_ids:
                reasons.setdefault(learner_id, []).append(reason)
    learners = Learner.objects.filter(pk__in=reasons).order_by("created_at")
    return [(learner, "; ".join(reasons[learner.pk])) for learner in learners]
