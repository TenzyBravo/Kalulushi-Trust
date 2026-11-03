from datetime import date

from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.text import slugify

from learners.models import Learner
from school.models import AcademicYear
from staffing.access import can_view_learner

from . import queries
from .models import ReportExport
from .tables import record_export, workbook


def _day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _deliver(request, result, scope_text, filters, report_key):
    """Show the report, or export it (Excel / print view). Every export is logged; learner-level ones audited (R2)."""
    fmt = request.GET.get("format")
    if fmt in ("xlsx", "print"):
        record_export(request.user, report_key, fmt, filters, result.row_count, result.sensitive)
        if fmt == "xlsx":
            response = HttpResponse(workbook(result.title, scope_text, result.tables),
                                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response["Content-Disposition"] = f'attachment; filename="{slugify(result.title)}.xlsx"'
            return response
        return render(request, "reports/print.html", {"result": result, "scope_text": scope_text, "printed": timezone.now()})
    return None


@login_required
@permission_required("reports.view_reports", raise_exception=True)
def index(request):
    return render(request, "reports/index.html", {"reports": queries.REPORTS})


@login_required
@permission_required("reports.view_reports", raise_exception=True)
def report(request, name):
    if name == "learner":
        return learner(request)
    if name not in queries.REPORTS:
        raise Http404
    title, _, build = queries.REPORTS[name]
    user = request.user
    scope = queries.build_scope(user, request.GET.get("year"), request.GET.get("term"),
                                _day(request.GET.get("from")), _day(request.GET.get("to")))
    if scope is None:
        return render(request, "reports/report.html", {"title": title, "result": None, "name": name})
    kwargs = {"grade_id": request.GET.get("grade"), "class_id": request.GET.get("cls")}
    if name == "performance":
        kwargs["subject_id"] = request.GET.get("subject")
    result = build(scope, **kwargs)
    filters = {**scope.filters(), **{k: v for k, v in kwargs.items() if v}}
    exported = _deliver(request, result, scope.text(), filters, name)
    if exported:
        return exported
    query = request.GET.copy()
    query.pop("format", None)
    return render(request, "reports/report.html", {
        "title": title, "name": name, "result": result, "scope": scope, "query": query.urlencode(),
        "years": AcademicYear.objects.filter(school=user.school), "terms": scope.year.terms.order_by("start_date"),
        "subjects": queries.subjects_for(scope) if name == "performance" else [],
        "subject_id": request.GET.get("subject", ""), "uses_term": name in ("performance", "competencies"),
    })


def learner(request):
    learner_obj = get_object_or_404(Learner, pk=request.GET.get("learner") or 0)
    if not can_view_learner(request.user, learner_obj):
        raise Http404  # same access as the profile (§11)
    result = queries.learner_report(request.user, learner_obj)
    scope_text = f"{learner_obj.learner_number} · printed {timezone.localdate():%d %b %Y}"
    exported = _deliver(request, result, scope_text, {"learner": learner_obj.learner_number}, "learner")
    if exported:
        return exported
    return render(request, "reports/learner.html", {"result": result, "learner": learner_obj,
                                                     "query": f"learner={learner_obj.pk}"})


@login_required
@permission_required("reports.view_reportexport", raise_exception=True)
def exports_log(request):
    exports = ReportExport.objects.filter(school=request.user.school).select_related("user")
    return render(request, "reports/exports.html", {"page": Paginator(exports, 50).get_page(request.GET.get("page"))})
