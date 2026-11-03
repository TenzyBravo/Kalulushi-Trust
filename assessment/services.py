"""Changes to grading, assessments, marks and competencies. Each runs in one transaction with its audit record."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core import audit
from learners.models import Enrollment

from . import access
from .calc import plan_for
from .models import (
    MISSING_STATUSES, Assessment, AssessmentPlan, AssessmentResult, AssessmentResultHistory, CompetencyFramework,
    CompetencySheet, GradeBand, GradingScheme, LearnerCompetencyResult,
)

HUNDRED = Decimal(100)


def enrolled_on(class_group, day):
    """Enrollments in the class on the day, learners in name order."""
    return list(
        Enrollment.objects.filter(class_group=class_group, start_date__lte=day)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).exclude(status="reversed")
        .select_related("learner").order_by("learner__last_name", "learner__first_name")
    )


def parse_score(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f"'{raw}' is not a number.")


# Grading schemes and plans (§3, §4) ---------------------------------------------


def check_band_coverage(scheme):
    """Bands must cover 0–100 with no gap wider than the scheme's rounding step."""
    bands = sorted(scheme.bands.all(), key=lambda b: b.min_percent)
    if not bands:
        raise ValidationError("Add grade bands before activating the scheme.")
    step = Decimal(1).scaleb(-scheme.decimal_places)
    if bands[0].min_percent != 0 or bands[-1].max_percent != HUNDRED:
        raise ValidationError("The bands must start at 0 and end at 100.")
    for lower, upper in zip(bands, bands[1:]):
        if upper.min_percent - lower.max_percent > step:
            raise ValidationError(f"No band covers scores between {lower.max_percent} and {upper.min_percent}.")


@transaction.atomic
def activate_scheme(scheme, actor):
    scheme = GradingScheme.objects.select_for_update().get(pk=scheme.pk)
    if scheme.status != GradingScheme.DRAFT:
        raise ValidationError("Only a draft scheme can be activated.")
    check_band_coverage(scheme)
    scheme.status, scheme.activated_by, scheme.activated_at = GradingScheme.ACTIVE, actor, timezone.now()
    scheme.save(update_fields=["status", "activated_by", "activated_at"])
    audit.record("grading_scheme.activate", scheme, actor=actor, school=scheme.school,
                 changes={"bands": [f"{b.label} {b.min_percent}-{b.max_percent}" for b in scheme.bands.all()]})
    return scheme


@transaction.atomic
def new_scheme_version(scheme, actor):
    """Copy a scheme and its bands into a new draft version (§3: active schemes are never edited)."""
    latest = GradingScheme.objects.filter(school=scheme.school, stage=scheme.stage, name=scheme.name).order_by("-version").first()
    copy = GradingScheme.objects.create(
        school=scheme.school, stage=scheme.stage, name=scheme.name, version=latest.version + 1,
        decimal_places=scheme.decimal_places, missing_rule=scheme.missing_rule, created_by=actor,
    )
    for band in scheme.bands.all():
        GradeBand.objects.create(scheme=copy, label=band.label, description=band.description, min_percent=band.min_percent,
                                 max_percent=band.max_percent, points=band.points, is_pass=band.is_pass)
    audit.record("grading_scheme.new_version", copy, actor=actor, school=scheme.school, changes={"from_version": scheme.version})
    return copy


@transaction.atomic
def activate_plan(plan, actor):
    """A plan needs an active scheme for its stage and weights adding up to 100 (decision B2)."""
    plan = AssessmentPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status == AssessmentPlan.ACTIVE:
        return plan
    components = list(plan.components.all())
    if not components:
        raise ValidationError("Add the plan's components first.")
    if any(c.weight is None for c in components):
        raise ValidationError("Enter a weight for every component before activating the plan.")
    total = sum(c.weight for c in components)
    if total != HUNDRED:
        raise ValidationError(f"The weights add up to {total}%, not 100%.")
    if plan.grading_scheme.status != GradingScheme.ACTIVE or plan.grading_scheme.stage_id != plan.stage_id:
        raise ValidationError("Choose an active grading scheme for this stage.")
    plan.status, plan.activated_by, plan.activated_at = AssessmentPlan.ACTIVE, actor, timezone.now()
    plan.save(update_fields=["status", "activated_by", "activated_at"])
    audit.record("assessment_plan.activate", plan, actor=actor, school=plan.school,
                 changes={"components": {c.name: str(c.weight) for c in components}, "scheme": str(plan.grading_scheme)})
    return plan


# Assessments and marks (§5) ------------------------------------------------------


@transaction.atomic
def create_assessment(actor, *, class_subject, term, component, assessment_type, title, max_score, assessed_on):
    if not access.can_enter_marks(actor, class_subject):
        raise PermissionDenied("You do not teach this class subject.")
    class_group = class_subject.class_group
    if term.academic_year_id != class_group.academic_year_id:
        raise ValidationError(f"{term} is not in {class_group.academic_year}.")
    if not (term.start_date <= assessed_on <= term.end_date):
        raise ValidationError(f"The assessment date must fall in {term} ({term.start_date} to {term.end_date}).")
    plan = plan_for(class_group, term)
    if plan is None:
        raise ValidationError(f"There is no assessment plan for {class_group.grade} in {term} yet. Ask the Academic Administrator.")
    if component.plan_id != plan.pk:
        raise ValidationError("Choose a component of this term's plan.")
    assessment = Assessment(
        school=class_group.school, class_subject=class_subject, term=term, component=component,
        assessment_type=assessment_type, title=title.strip(), max_score=max_score, assessed_on=assessed_on, created_by=actor,
    )
    assessment.full_clean()
    assessment.save()
    audit.record("assessment.create", assessment, actor=actor, school=assessment.school,
                 changes={"class_subject": str(class_subject), "title": assessment.title, "max": max_score})
    return assessment


def _check_entry(assessment, score, missing):
    if missing and missing not in MISSING_STATUSES:
        raise ValidationError("Unknown missing status.")
    if score is not None and missing:
        raise ValidationError("Give a mark or a missing status, not both.")
    if score is not None and not (0 <= score <= assessment.max_score):
        raise ValidationError(f"Marks must be between 0 and {assessment.max_score}.")


@transaction.atomic
def save_draft(actor, assessment, entries):
    """Save some or all learners' marks while the assessment is a draft.

    entries: {learner_id: (score or None, missing_status or "", comment)}. Returns per-learner errors.
    """
    assessment = Assessment.objects.select_for_update().get(pk=assessment.pk)
    if not access.can_enter_marks(actor, assessment.class_subject):
        raise PermissionDenied
    if assessment.status != Assessment.DRAFT:
        raise ValidationError("This assessment is submitted; changes are corrections, with a reason.")
    roster = {e.learner_id: e for e in enrolled_on(assessment.class_subject.class_group, assessment.assessed_on)}
    existing = {r.learner_id: r for r in assessment.results.all()}
    errors = {}
    for learner_id, (score, missing, comment) in entries.items():
        enrollment = roster.get(learner_id)
        if enrollment is None:
            errors[learner_id] = "Not in this class on the assessment date."
            continue
        try:
            _check_entry(assessment, score, missing)
        except ValidationError as exc:
            errors[learner_id] = exc.messages[0]
            continue
        result = existing.get(learner_id)
        if result is None:
            AssessmentResult.objects.create(
                assessment=assessment, class_subject=assessment.class_subject,
                class_group=assessment.class_subject.class_group, enrollment=enrollment, learner=enrollment.learner,
                score=score, missing_status=missing, comment=(comment or "").strip(), updated_by=actor,
            )
        elif (result.score, result.missing_status, result.comment) != (score, missing, (comment or "").strip()):
            result.score, result.missing_status, result.comment = score, missing, (comment or "").strip()
            result.updated_by = actor
            result.save(update_fields=["score", "missing_status", "comment", "updated_by", "updated_at"])
    return errors


@transaction.atomic
def submit(actor, assessment):
    """Submit the whole class at once: every learner needs a mark or a missing status (§7)."""
    assessment = Assessment.objects.select_for_update().get(pk=assessment.pk)
    if not access.can_enter_marks(actor, assessment.class_subject):
        raise PermissionDenied
    if assessment.status != Assessment.DRAFT:
        return assessment
    roster = enrolled_on(assessment.class_subject.class_group, assessment.assessed_on)
    results = {r.learner_id: r for r in assessment.results.all()}
    incomplete = [e.learner.full_name for e in roster if not (results.get(e.learner_id) and results[e.learner_id].is_complete)]
    if incomplete:
        raise ValidationError(f"Give a mark or a missing status for: {', '.join(incomplete)}.")
    if not roster:
        raise ValidationError("No learners are enrolled in this class on the assessment date.")
    assessment.status, assessment.submitted_by, assessment.submitted_at = Assessment.SUBMITTED, actor, timezone.now()
    assessment.save(update_fields=["status", "submitted_by", "submitted_at", "updated_at"])
    missing = sum(1 for r in results.values() if r.missing_status)
    audit.record("assessment.submit", assessment, actor=actor, school=assessment.school,
                 changes={"learners": len(roster), "missing": missing})
    return assessment


@transaction.atomic
def correct(actor, result, *, score, missing_status, comment, reason):
    """Change a submitted mark: history row and audit record in the same transaction (§5, decision B8)."""
    result = AssessmentResult.objects.select_for_update().select_related("assessment__class_subject").get(pk=result.pk)
    assessment = result.assessment
    if assessment.status != Assessment.SUBMITTED:
        raise ValidationError("This assessment is still a draft; just edit it.")
    if not access.can_correct_marks(actor, assessment.class_subject):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError("Give a reason for the correction.")
    if score is None and not missing_status:
        raise ValidationError("A submitted result needs a mark or a missing status.")
    _check_entry(assessment, score, missing_status)
    comment = (comment or "").strip()
    if (result.score, result.missing_status, result.comment) == (score, missing_status, comment):
        return result
    AssessmentResultHistory.objects.create(
        result=result, old_score=result.score, new_score=score, old_missing_status=result.missing_status,
        new_missing_status=missing_status, old_comment=result.comment, new_comment=comment, reason=reason.strip(),
        changed_by=actor,
    )
    old = {"score": result.score, "missing": result.missing_status}
    result.score, result.missing_status, result.comment, result.updated_by = score, missing_status, comment, actor
    result.save(update_fields=["score", "missing_status", "comment", "updated_by", "updated_at"])
    audit.record("assessment_result.correct", result, actor=actor, school=assessment.school, reason=reason,
                 changes={"learner": result.learner.learner_number, "assessment": str(assessment),
                          **audit.diff(old, {"score": score, "missing": missing_status})})
    return result


# Competencies (§7) -----------------------------------------------------------------


def active_framework(stage):
    return CompetencyFramework.objects.filter(stage=stage, status=CompetencyFramework.ACTIVE).prefetch_related(
        "competencies", "levels").first()


@transaction.atomic
def activate_framework(framework, actor):
    framework = CompetencyFramework.objects.select_for_update().get(pk=framework.pk)
    if framework.status != CompetencyFramework.DRAFT:
        raise ValidationError("Only a draft framework can be activated.")
    if not framework.competencies.exists() or not framework.levels.exists():
        raise ValidationError("Add the competencies and levels first.")
    CompetencyFramework.objects.filter(school=framework.school, stage=framework.stage, status=CompetencyFramework.ACTIVE) \
        .update(status=CompetencyFramework.RETIRED)
    framework.status, framework.activated_at = CompetencyFramework.ACTIVE, timezone.now()
    framework.save(update_fields=["status", "activated_at"])
    audit.record("competency_framework.activate", framework, actor=actor, school=framework.school)
    return framework


def sheet_for(class_group, term, create=False, actor=None):
    sheet = CompetencySheet.objects.filter(class_group=class_group, term=term).select_related("framework").first()
    if sheet or not create:
        return sheet
    framework = active_framework(class_group.grade.stage)
    if framework is None:
        raise ValidationError(f"No competency framework is active for {class_group.grade.stage} yet. Ask the Academic Administrator.")
    with transaction.atomic():
        sheet = CompetencySheet.objects.create(school=class_group.school, class_group=class_group, term=term, framework=framework)
        audit.record("competency_sheet.create", sheet, actor=actor, school=class_group.school)
    return sheet


@transaction.atomic
def save_competencies(actor, sheet, entries):
    """entries: {(learner_id, competency_id): (level_id or None, not_assessed, comment)} while the sheet is a draft."""
    sheet = CompetencySheet.objects.select_for_update().get(pk=sheet.pk)
    if not access.can_record_competencies(actor, sheet.class_group):
        raise PermissionDenied
    if sheet.status != CompetencySheet.DRAFT:
        raise ValidationError("This sheet is submitted; changes are corrections, with a reason.")
    roster = {e.learner_id: e for e in enrolled_on(sheet.class_group, min(sheet.term.end_date, timezone.localdate()))}
    competencies = {c.pk for c in sheet.framework.competencies.all()}
    levels = {l.pk for l in sheet.framework.levels.all()}
    existing = {(r.learner_id, r.competency_id): r for r in sheet.results.all()}
    for (learner_id, competency_id), (level_id, not_assessed, comment) in entries.items():
        if learner_id not in roster or competency_id not in competencies or (level_id and level_id not in levels):
            raise ValidationError("The sheet changed; reload the page.")
        if level_id and not_assessed:
            level_id = None
        result = existing.get((learner_id, competency_id))
        values = {"level_id": level_id, "not_assessed": bool(not_assessed), "comment": (comment or "").strip(), "updated_by": actor}
        if result is None:
            LearnerCompetencyResult.objects.create(sheet=sheet, competency_id=competency_id, class_group=sheet.class_group,
                                                   enrollment=roster[learner_id], learner=roster[learner_id].learner, **values)
        else:
            for k, v in values.items():
                setattr(result, k, v)
            result.save()
    return sheet


@transaction.atomic
def submit_competencies(actor, sheet):
    sheet = CompetencySheet.objects.select_for_update().get(pk=sheet.pk)
    if not access.can_record_competencies(actor, sheet.class_group):
        raise PermissionDenied
    roster = enrolled_on(sheet.class_group, min(sheet.term.end_date, timezone.localdate()))
    cells = {(r.learner_id, r.competency_id): r for r in sheet.results.all()}
    competencies = list(sheet.framework.competencies.all())
    gaps = sorted({e.learner.full_name for e in roster for c in competencies
                   if not (cells.get((e.learner_id, c.pk)) and cells[(e.learner_id, c.pk)].is_complete)})
    if gaps:
        raise ValidationError(f"Choose a level or Not assessed for every competency: {', '.join(gaps)}.")
    sheet.status, sheet.submitted_by, sheet.submitted_at = CompetencySheet.SUBMITTED, actor, timezone.now()
    sheet.save(update_fields=["status", "submitted_by", "submitted_at"])
    not_assessed = sum(1 for r in cells.values() if r.not_assessed)
    audit.record("competency_sheet.submit", sheet, actor=actor, school=sheet.school,
                 changes={"learners": len(roster), "not_assessed": not_assessed})
    return sheet


@transaction.atomic
def correct_competency(actor, result, *, level_id, not_assessed, comment, reason):
    result = LearnerCompetencyResult.objects.select_for_update().select_related("sheet").get(pk=result.pk)
    if result.sheet.status != CompetencySheet.SUBMITTED:
        raise ValidationError("This sheet is still a draft; just edit it.")
    if not access.can_correct_competencies(actor, result.sheet.class_group):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError("Give a reason for the correction.")
    if not level_id and not not_assessed:
        raise ValidationError("Choose a level or Not assessed.")
    old = {"level": result.level_id, "not_assessed": result.not_assessed}
    result.level_id, result.not_assessed = (None, True) if not_assessed else (level_id, False)
    result.comment, result.updated_by = (comment or "").strip(), actor
    result.save()
    audit.record("competency_result.correct", result, actor=actor, school=result.sheet.school, reason=reason,
                 changes={"learner": result.learner.learner_number, "competency": str(result.competency),
                          **audit.diff(old, {"level": result.level_id, "not_assessed": result.not_assessed})})
    return result
