import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from attendance.access import register_classes
from core.models import SchoolSettings
from school.models import AcademicYear, ClassGroup, ClassSubject, Term
from staffing.access import sees_whole_school

from . import access, calc, services
from .forms import MISSING_CHOICES, AssessmentForm, CompetencyCorrectionForm, CorrectionForm
from .models import Assessment, AssessmentResult, CompetencySheet, LearnerCompetencyResult


def current_term(school, term_id=None):
    if term_id and str(term_id).isdigit():
        term = Term.objects.filter(pk=term_id, academic_year__school=school).select_related("academic_year").first()
        if term:
            return term
    year = AcademicYear.current(school)
    if year is None:
        return None
    today = timezone.localdate()
    terms = list(year.terms.order_by("start_date"))
    return (next((t for t in terms if t.start_date <= today <= t.end_date), None)
            or next((t for t in reversed(terms) if t.start_date <= today), None)
            or (terms[0] if terms else None))


def _messages(exc):
    return exc.messages if isinstance(exc, ValidationError) else [str(exc) or "Not allowed."]


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def my_subjects(request):
    user = request.user
    term = current_term(user.school, request.GET.get("term"))
    if sees_whole_school(user):
        class_subjects = ClassSubject.objects.filter(class_group__academic_year=term.academic_year) if term else ClassSubject.objects.none()
    else:
        class_subjects = access.teachable_class_subjects(user)
    class_subjects = (class_subjects.filter(status="active").select_related("class_group__grade", "subject")
                      .annotate(n=Count("assessments", filter=Q(assessments__term=term)),
                                drafts=Count("assessments", filter=Q(assessments__term=term, assessments__status="draft")))
                      .order_by("class_group__grade__sequence", "class_group__stream", "subject__name"))
    my_classes = list(register_classes(user)) if not sees_whole_school(user) else []
    year = term.academic_year if term else None
    all_classes = list(year.classes.filter(status="active").select_related("grade")) if year and sees_whole_school(user) else []
    return render(request, "assessment/my_subjects.html", {
        "term": term, "terms": Term.objects.filter(academic_year=year) if year else [],
        "class_subjects": class_subjects, "my_classes": my_classes, "all_classes": all_classes,
    })


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def subject(request, pk):
    class_subject = get_object_or_404(ClassSubject.objects.select_related("class_group__grade", "class_group__school", "subject"), pk=pk)
    user = request.user
    if not access.can_view_class_subject(user, class_subject):
        raise Http404
    term = current_term(user.school, request.GET.get("term") or request.POST.get("term"))
    if term is None:
        raise Http404
    plan = calc.plan_for(class_subject.class_group, term)
    can_enter = access.can_enter_marks(user, class_subject)
    form = AssessmentForm(request.POST or None, components=plan.components.all() if plan else [],
                          initial={"assessed_on": min(timezone.localdate(), term.end_date)})
    if request.method == "POST":
        if not can_enter:
            raise PermissionDenied
        if form.is_valid():
            d = form.cleaned_data
            try:
                assessment = services.create_assessment(
                    user, class_subject=class_subject, term=term, component=d["component"],
                    assessment_type=d["assessment_type"], title=d["title"], max_score=d["max_score"], assessed_on=d["assessed_on"],
                )
            except (ValidationError, PermissionDenied) as exc:
                for m in _messages(exc):
                    form.add_error(None, m)
            else:
                messages.success(request, f"Created {assessment.title}. Enter the marks below.")
                return redirect("assessment_marks", pk=assessment.pk)
    assessments = list(class_subject.assessments.filter(term=term).select_related("component", "assessment_type"))
    roster = services.enrolled_on(class_subject.class_group, min(timezone.localdate(), term.end_date))
    results = {(r.assessment_id, r.learner_id): r for r in AssessmentResult.objects.filter(assessment__in=assessments)}
    rows = []
    for e in roster:
        cells = []
        for a in assessments:
            r = results.get((a.pk, e.learner_id))
            cells.append(r)
        rows.append((e.learner, cells, calc.subject_score(e.learner, class_subject, term, plan=plan)))
    return render(request, "assessment/subject.html", {
        "class_subject": class_subject, "term": term, "plan": plan, "assessments": assessments, "rows": rows,
        "form": form, "can_enter": can_enter, "terms": Term.objects.filter(academic_year=class_subject.class_group.academic_year),
    })


def _assessment(request, pk):
    assessment = get_object_or_404(
        Assessment.objects.select_related("class_subject__class_group__grade", "class_subject__subject", "term", "component"), pk=pk)
    if not access.can_view_class_subject(request.user, assessment.class_subject):
        raise Http404
    return assessment


def _entries_from_post(post, learner_ids):
    entries, errors = {}, {}
    for learner_id in learner_ids:
        if f"score_{learner_id}" not in post and f"missing_{learner_id}" not in post:
            continue
        try:
            score = services.parse_score(post.get(f"score_{learner_id}"))
        except ValidationError as exc:
            errors[learner_id] = exc.messages[0]
            continue
        entries[learner_id] = (score, post.get(f"missing_{learner_id}", ""), post.get(f"comment_{learner_id}", ""))
    return entries, errors


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def marks(request, pk):
    """Class table for one assessment (§9 screen 3): draft entry, Save draft, Submit."""
    assessment = _assessment(request, pk)
    user = request.user
    can_enter = access.can_enter_marks(user, assessment.class_subject) and assessment.status == Assessment.DRAFT
    roster = services.enrolled_on(assessment.class_subject.class_group, assessment.assessed_on)
    errors, form_errors = {}, []
    if request.method == "POST":
        if not can_enter:
            raise PermissionDenied
        entries, errors = _entries_from_post(request.POST, [e.learner_id for e in roster])
        try:
            errors.update(services.save_draft(user, assessment, entries))
            if request.POST.get("action") == "submit" and not errors:
                services.submit(user, assessment)
                messages.success(request, f"{assessment.title} submitted for {assessment.class_subject.class_group.name}.")
                return redirect("assessment_subject", pk=assessment.class_subject_id)
            if not errors:
                messages.success(request, "Draft saved.")
                return redirect("assessment_marks", pk=assessment.pk)
        except (ValidationError, PermissionDenied) as exc:
            form_errors = _messages(exc)
    results = {r.learner_id: r for r in assessment.results.all()}
    post = request.POST if request.method == "POST" else None
    rows = []
    for e in roster:
        r = results.get(e.learner_id)
        lid = e.learner_id
        rows.append({
            "learner": e.learner, "result": r, "error": errors.get(lid, ""),
            "score": post.get(f"score_{lid}", "") if post else ("" if not r or r.score is None else f"{r.score.normalize():f}"),
            "missing": post.get(f"missing_{lid}", "") if post else (r.missing_status if r else ""),
            "comment": post.get(f"comment_{lid}", "") if post else (r.comment if r else ""),
        })
    return render(request, "assessment/marks.html", {
        "assessment": assessment, "rows": rows, "can_enter": can_enter, "form_errors": form_errors,
        "missing_choices": MISSING_CHOICES,
        "can_correct": assessment.status == Assessment.SUBMITTED and access.can_correct_marks(user, assessment.class_subject),
        "complete": sum(1 for row in rows if row["result"] and row["result"].is_complete),
    })


@login_required
@require_POST
def marks_save(request, pk):
    """Auto-save one learner's draft mark (Saving / Saved / Save failed)."""
    assessment = _assessment(request, pk)
    try:
        data = json.loads(request.body or b"{}")
        learner_id = int(data.get("learner"))
        score = services.parse_score(str(data.get("score") or ""))
        errors = services.save_draft(request.user, assessment, {learner_id: (score, data.get("missing") or "", data.get("comment") or "")})
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Could not read the entry."}, status=400)
    except (ValidationError, PermissionDenied) as exc:
        return JsonResponse({"ok": False, "error": _messages(exc)[0]}, status=400)
    if errors:
        return JsonResponse({"ok": False, "error": errors[learner_id]}, status=400)
    return JsonResponse({"ok": True})


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def learner_marks(request, pk, learner_pk):
    """One learner's result: detailed comment while a draft, or a correction with a reason after submission."""
    assessment = _assessment(request, pk)
    user = request.user
    enrollment = next((e for e in services.enrolled_on(assessment.class_subject.class_group, assessment.assessed_on)
                       if e.learner_id == learner_pk), None)
    if enrollment is None:
        raise Http404
    result = assessment.results.filter(learner_id=learner_pk).first()
    submitted = assessment.status == Assessment.SUBMITTED
    allowed = access.can_correct_marks(user, assessment.class_subject) if submitted else access.can_enter_marks(user, assessment.class_subject)
    initial = {"score": result.score if result else None, "missing_status": result.missing_status if result else "",
               "comment": result.comment if result else ""}
    form = CorrectionForm(request.POST or None, initial=initial)
    if request.method == "POST":
        if not allowed:
            raise PermissionDenied
        if form.is_valid():
            d = form.cleaned_data
            try:
                if submitted:
                    services.correct(user, result, score=d["score"], missing_status=d["missing_status"],
                                     comment=d["comment"], reason=d["reason"])
                else:
                    errors = services.save_draft(user, assessment, {learner_pk: (d["score"], d["missing_status"], d["comment"])})
                    if errors:
                        raise ValidationError(errors[learner_pk])
            except (ValidationError, PermissionDenied) as exc:
                for m in _messages(exc):
                    form.add_error(None, m)
            else:
                messages.success(request, "Correction saved and recorded." if submitted else "Saved.")
                return redirect("assessment_marks", pk=assessment.pk)
    return render(request, "assessment/learner_marks.html", {
        "assessment": assessment, "learner": enrollment.learner, "result": result, "form": form,
        "allowed": allowed, "submitted": submitted, "history": result.history.select_related("changed_by") if result else [],
    })


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def class_results(request, pk):
    """Every subject score and the overall term score for a class, with positions when rankings are on."""
    class_group = get_object_or_404(ClassGroup.objects.select_related("grade", "academic_year"), pk=pk)
    if not access.can_view_class(request.user, class_group):
        raise Http404
    term = current_term(request.user.school, request.GET.get("term"))
    if term is None:
        raise Http404
    learners = [e.learner for e in services.enrolled_on(class_group, min(timezone.localdate(), term.end_date))]
    class_subjects, rows = calc.class_term_results(class_group, term, learners)
    rankings = SchoolSettings.for_school(class_group.school).rankings_enabled
    if rankings:
        ranks = calc.competition_ranks([(r.learner.pk, r.overall) for r in rows])
        for r in rows:
            r.position = ranks.get(r.learner.pk)
    return render(request, "assessment/class_results.html", {
        "class_group": class_group, "term": term, "class_subjects": class_subjects, "rows": rows, "rankings": rankings,
        "plan": calc.plan_for(class_group, term), "terms": Term.objects.filter(academic_year=class_group.academic_year),
    })


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def competencies(request, pk):
    """End-of-term competency sheet (§9 screen 4): learners as rows, competencies as columns."""
    class_group = get_object_or_404(ClassGroup.objects.select_related("grade__stage"), pk=pk)
    user = request.user
    if not access.can_view_class(user, class_group):
        raise Http404
    term = current_term(user.school, request.GET.get("term") or request.POST.get("term"))
    if term is None:
        raise Http404
    can_record = access.can_record_competencies(user, class_group)
    error = ""
    sheet = services.sheet_for(class_group, term)
    framework = sheet.framework if sheet else services.active_framework(class_group.grade.stage)
    if framework is None:
        error = f"No competency framework is active for {class_group.grade.stage} yet. The Academic Administrator sets it up in School setup."
    editable = framework is not None and can_record and (sheet is None or sheet.status == CompetencySheet.DRAFT)
    roster = services.enrolled_on(class_group, min(timezone.localdate(), term.end_date))
    if request.method == "POST" and framework is not None:
        if not editable:
            raise PermissionDenied
        sheet = sheet or services.sheet_for(class_group, term, create=True, actor=user)  # created on first save
        entries = {}
        for e in roster:
            for c in sheet.framework.competencies.all():
                value = request.POST.get(f"c_{e.learner_id}_{c.pk}", "")
                if value or f"c_{e.learner_id}_{c.pk}" in request.POST:
                    entries[(e.learner_id, c.pk)] = (int(value) if value.isdigit() else None, value == "not_assessed", "")
        try:
            services.save_competencies(user, sheet, entries)
            if request.POST.get("action") == "submit":
                services.submit_competencies(user, sheet)
                messages.success(request, "Competencies submitted.")
            else:
                messages.success(request, "Draft saved.")
            return redirect(f"{reverse('assessment_competencies', args=[class_group.pk])}?term={term.pk}")
        except (ValidationError, PermissionDenied) as exc:
            error = _messages(exc)[0]
    cells = {(r.learner_id, r.competency_id): r for r in sheet.results.all()} if sheet else {}
    comps = list(framework.competencies.all()) if framework else []
    rows = [(e.learner, [(c, cells.get((e.learner_id, c.pk))) for c in comps]) for e in roster]
    return render(request, "assessment/competencies.html", {
        "class_group": class_group, "term": term, "sheet": sheet, "framework": framework, "competencies": comps, "rows": rows,
        "levels": list(framework.levels.all()) if framework else [], "editable": editable, "error": error,
        "can_correct": bool(sheet) and sheet.status == CompetencySheet.SUBMITTED and access.can_correct_competencies(user, class_group),
        "terms": Term.objects.filter(academic_year=class_group.academic_year),
    })


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def competency_learner(request, sheet_pk, learner_pk):
    sheet = get_object_or_404(CompetencySheet.objects.select_related("class_group", "framework", "term"), pk=sheet_pk)
    user = request.user
    if sheet.status != CompetencySheet.SUBMITTED or not access.can_correct_competencies(user, sheet.class_group):
        raise PermissionDenied
    results = list(LearnerCompetencyResult.objects.filter(sheet=sheet, learner_id=learner_pk).select_related("competency", "level", "learner"))
    if not results:
        raise Http404
    competency_id = request.GET.get("competency") or request.POST.get("competency") or str(results[0].competency_id)
    result = next((r for r in results if str(r.competency_id) == competency_id), results[0])
    levels = list(sheet.framework.levels.all())
    initial = {"level": "not_assessed" if result.not_assessed else (str(result.level_id) if result.level_id else ""), "comment": result.comment}
    form = CompetencyCorrectionForm(request.POST or None, levels=levels, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.correct_competency(user, result, level_id=int(d["level"]) if d["level"].isdigit() else None,
                                        not_assessed=d["level"] == "not_assessed", comment=d["comment"], reason=d["reason"])
        except (ValidationError, PermissionDenied) as exc:
            for m in _messages(exc):
                form.add_error(None, m)
        else:
            messages.success(request, "Correction saved and recorded.")
            return redirect(f"{reverse('assessment_competencies', args=[sheet.class_group_id])}?term={sheet.term_id}")
    return render(request, "assessment/competency_learner.html", {
        "sheet": sheet, "learner": result.learner, "result": result, "results": results, "form": form,
    })


@login_required
@permission_required("assessment.view_assessment", raise_exception=True)
def incomplete(request):
    """Missing and incomplete assessments (§9 screen 5)."""
    user = request.user
    term = current_term(user.school, request.GET.get("term"))
    if term is None:
        raise Http404
    assessments = Assessment.objects.filter(term=term).select_related("class_subject__class_group", "class_subject__subject")
    classes = ClassGroup.objects.filter(academic_year=term.academic_year, status="active").select_related("grade")
    if not sees_whole_school(user):
        assessments = assessments.filter(class_subject__in=access.teachable_class_subjects(user))
        classes = classes.filter(pk__in=[c.pk for c in register_classes(user)])
    drafts = assessments.filter(status=Assessment.DRAFT).order_by("assessed_on")
    with_missing = (assessments.filter(status=Assessment.SUBMITTED)
                    .annotate(missing=Count("results", filter=~Q(results__missing_status="")))
                    .filter(missing__gt=0).order_by("class_subject__class_group__name"))
    sheets = {s.class_group_id: s for s in CompetencySheet.objects.filter(term=term).annotate(
        not_assessed=Count("results", filter=Q(results__not_assessed=True)))}
    return render(request, "assessment/incomplete.html", {
        "term": term, "terms": Term.objects.filter(academic_year=term.academic_year),
        "drafts": drafts, "with_missing": with_missing,
        "sheet_rows": [(c, sheets.get(c.pk)) for c in classes.order_by("grade__sequence", "stream")],
    })
