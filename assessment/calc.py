"""Subject and overall term scores, grades and positions (slice 3 design §6).

Nothing here is stored: everything is calculated from marks, the plan and the
grading scheme. A missing result is never counted as zero.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .models import Assessment, AssessmentPlan, AssessmentResult

HUNDRED = Decimal(100)


@dataclass
class SubjectScore:
    class_subject: object
    percent: Decimal | None = None
    band: object = None
    pending: str = ""  # why there is no score yet
    components: list = field(default_factory=list)  # [(component, percent or None, note)]

    @property
    def complete(self):
        return self.percent is not None


def round_percent(value, places):
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def plan_for(class_group, term):
    """The plan in force: a grade plan overrides the stage plan."""
    plans = AssessmentPlan.objects.filter(term=term).select_related("grading_scheme").prefetch_related(
        "components", "grading_scheme__bands")
    return (plans.filter(grade=class_group.grade).first()
            or plans.filter(stage=class_group.grade.stage_id, grade__isnull=True).first())


def subject_score(learner, class_subject, term, plan=None, results=None):
    """The learner's term score in one class subject."""
    plan = plan if plan is not None else plan_for(class_subject.class_group, term)
    score = SubjectScore(class_subject)
    if plan is None:
        score.pending = "no assessment plan for this term"
        return score
    if results is None:
        results = list(
            AssessmentResult.objects.filter(
                learner=learner, assessment__class_subject=class_subject, assessment__term=term,
                assessment__status=Assessment.SUBMITTED,
            ).select_related("assessment")
        )
    scheme = plan.grading_scheme
    total, blocked = Decimal(0), []
    for component in plan.components.all():
        mine = [r for r in results if r.assessment.component_id == component.pk]
        percents, missing = [], []
        for r in mine:
            if r.score is not None:
                percents.append(r.score / r.assessment.max_score * HUNDRED)
            elif r.missing_status:
                missing.append(r.missing_status)
        if scheme.missing_rule == "exclude_excused" and percents:
            missing = [m for m in missing if m != AssessmentResult.EXCUSED]
        if missing:
            note = f"missing ({', '.join(sorted(set(m.replace('_', ' ') for m in missing)))})"
            score.components.append((component, None, note))
            if component.required:
                blocked.append(f"{component.name}: {note}")
            continue
        if not percents:
            score.components.append((component, None, "no submitted assessment"))
            if component.required:
                blocked.append(f"{component.name}: no submitted assessment")
            continue
        average = sum(percents) / len(percents)  # decision B3: average of percentages
        score.components.append((component, round_percent(average, 2), ""))
        if component.weight is not None:
            total += average * component.weight / HUNDRED
    if plan.status != AssessmentPlan.ACTIVE or any(c.weight is None for c in plan.components.all()):
        score.pending = "weights not set: the assessment plan is not active yet"
        return score
    if blocked:
        score.pending = "; ".join(blocked)
        return score
    score.percent = round_percent(total, scheme.decimal_places)
    score.band = scheme.band_for(score.percent)
    return score


@dataclass
class TermResult:
    learner: object
    subjects: list
    overall: Decimal | None = None
    position: int | None = None

    @property
    def pending(self):
        return self.overall is None


def class_term_results(class_group, term, learners):
    """Every learner's subject scores and overall term score (decision B5) for the class."""
    plan = plan_for(class_group, term)
    class_subjects = list(class_group.class_subjects.filter(status="active").select_related("subject").order_by("subject__name"))
    all_results = list(
        AssessmentResult.objects.filter(
            assessment__class_subject__in=class_subjects, assessment__term=term, assessment__status=Assessment.SUBMITTED,
            learner__in=learners,
        ).select_related("assessment")
    )
    places = plan.grading_scheme.decimal_places if plan else 0
    rows = []
    for learner in learners:
        subjects = []
        for cs in class_subjects:
            mine = [r for r in all_results if r.learner_id == learner.pk and r.assessment.class_subject_id == cs.pk]
            subjects.append(subject_score(learner, cs, term, plan=plan, results=mine))
        overall = None
        if subjects and all(s.complete for s in subjects):
            overall = round_percent(sum(s.percent for s in subjects) / len(subjects), places)
        rows.append(TermResult(learner, subjects, overall))
    return class_subjects, rows


def competition_ranks(values):
    """1, 2, 2, 4 for ties (baseline §8). values: [(key, score or None)]; None gets no position."""
    scored = sorted((v for v in values if v[1] is not None), key=lambda v: v[1], reverse=True)
    ranks, previous, position = {}, None, 0
    for index, (key, score) in enumerate(scored, start=1):
        if score != previous:
            position, previous = index, score
        ranks[key] = position
    return ranks
