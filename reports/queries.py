"""Report figures, calculated from source records each time (reports design §1).

Each report returns a Result: tables for the requested level (school, grade,
class) plus whether it holds learner-level personal data (decision R2).
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from assessment import calc
from assessment.models import CompetencySheet, LearnerCompetencyResult
from attendance.access import is_class_teacher
from attendance.calendar import SchoolCalendar
from attendance.models import AbsenceCase, AttendanceRecord, AttendanceSession, FollowUpAction
from learners.models import Enrollment, EnrollmentRequest
from school.models import AcademicYear, ClassGroup, Grade, Subject, Term
from staffing.access import class_ids, sees_whole_school

from .tables import Cell, Table, pct, pct_text

STATUSES = ["present", "late", "absent", "excused"]


@dataclass
class Scope:
    user: object
    year: object
    term: object
    start: object
    end: object
    whole_school: bool
    classes: list = field(default_factory=list)  # classes this user may report on, in the year

    def filters(self):
        return {"year": str(self.year), "term": str(self.term) if self.term else "", "from": str(self.start), "to": str(self.end)}

    def text(self):
        return f"{self.year}{f', {self.term}' if self.term else ''} · {self.start:%d %b %Y} to {self.end:%d %b %Y}"


@dataclass
class Result:
    title: str
    tables: list
    sensitive: bool = False
    level: str = "school"
    crumbs: list = field(default_factory=list)  # [(label, url)]

    @property
    def row_count(self):
        return sum(len(t.rows) for t in self.tables)


def build_scope(user, year_id=None, term_id=None, start=None, end=None):
    years = AcademicYear.objects.filter(school=user.school)
    year = years.filter(pk=year_id).first() if str(year_id or "").isdigit() else None
    year = year or AcademicYear.current(user.school) or years.first()
    if year is None:
        return None
    terms = list(year.terms.order_by("start_date"))
    today = timezone.localdate()
    term = next((t for t in terms if str(t.pk) == str(term_id)), None) if term_id else None
    if term is None and not (start and end) and term_id != "year":
        term = next((t for t in terms if t.start_date <= today <= t.end_date), None) \
            or next((t for t in reversed(terms) if t.start_date <= today), None) or (terms[0] if terms else None)
    start = start or (term.start_date if term else year.start_date)
    end = end or (term.end_date if term else year.end_date)
    whole = sees_whole_school(user)
    classes = year.classes.filter(status="active").select_related("grade__stage")
    if not whole:
        classes = classes.filter(pk__in=class_ids(user))
    return Scope(user, year, term, start, end, whole, list(classes.order_by("grade__sequence", "stream")))


def _url(report, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    return f"{reverse('report', args=[report])}?{query}"


def _by_grade(classes):
    grouped = defaultdict(list)
    for c in classes:
        grouped[c.grade].append(c)
    return sorted(grouped.items(), key=lambda kv: kv[0].sequence)


def _by_stage(classes):
    stages = defaultdict(list)
    for grade, cs in _by_grade(classes):
        stages[grade.stage].append((grade, cs))
    return sorted(stages.items(), key=lambda kv: kv[0].sequence)


def _pick(scope, grade_id=None, class_id=None):
    """Narrow the scope to a grade or class the user may see. Returns (level, grade, class, classes)."""
    if class_id:
        c = next((c for c in scope.classes if str(c.pk) == str(class_id)), None)
        if c:
            return "class", c.grade, c, [c]
    if grade_id:
        cs = [c for c in scope.classes if str(c.grade_id) == str(grade_id)]
        if cs:
            return "grade", cs[0].grade, None, cs
    if scope.whole_school:
        return "school", None, None, scope.classes
    return "mine", None, None, scope.classes


def _crumbs(report, scope, level, grade, cls):
    base = {"year": scope.year.pk, "term": scope.term.pk if scope.term else "year"}
    crumbs = [("Whole school" if scope.whole_school else "My classes", _url(report, **base))]
    if grade and scope.whole_school:
        crumbs.append((grade.name, _url(report, grade=grade.pk, **base)))
    if cls:
        crumbs.append((f"Class {cls.name}", _url(report, cls=cls.pk, **base)))
    return crumbs


def _enrolled(classes, day):
    return list(
        Enrollment.objects.filter(class_group__in=classes, start_date__lte=day)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).exclude(status="reversed")
        .select_related("learner", "class_group").order_by("learner__last_name", "learner__first_name")
    )


# 1. Attendance ------------------------------------------------------------------


def _attendance_counts(classes, start, end):
    by_class = defaultdict(Counter)
    for row in (AttendanceRecord.objects.filter(class_group__in=classes, session__date__range=(start, end))
                .values("class_group", "learner__gender", "status").annotate(n=Count("id"))):
        c = by_class[row["class_group"]]
        c[row["status"]] += row["n"]
        c[f"{row['learner__gender'] or '?'}_{row['status']}"] += row["n"]
    taken = dict(AttendanceSession.objects.filter(class_group__in=classes, date__range=(start, end))
                 .values_list("class_group").annotate(n=Count("id")))
    until = min(end, timezone.localdate())
    expected = {}
    if classes:
        cal = SchoolCalendar(classes[0].school, start, until)
        days = [start + timedelta(days=i) for i in range((until - start).days + 1)] if until >= start else []
        for c in classes:
            expected[c.pk] = sum(1 for d in days if cal.is_register_day(d, c))
    return by_class, taken, expected


def _attendance_row(label, counts, taken, expected):
    total = sum(counts[s] for s in STATUSES)
    attended = counts["present"] + counts["late"]
    f_total = sum(counts[f"F_{s}"] for s in STATUSES)
    m_total = sum(counts[f"M_{s}"] for s in STATUSES)
    return [label, f"{taken} / {expected}", counts["present"], counts["late"], counts["absent"], counts["excused"],
            Cell(pct_text(attended, total), tone=_tone(pct(attended, total), 90, 80)),
            pct_text(counts["F_present"] + counts["F_late"], f_total), pct_text(counts["M_present"] + counts["M_late"], m_total)]


def _tone(value, good, warn):
    if value is None:
        return ""
    return "good" if value >= good else ("warn" if value >= warn else "bad")


ATT_COLUMNS = ["", "Registers taken / expected", "Present", "Late", "Absent", "Excused", "Attendance rate", "Girls", "Boys"]


def attendance(scope, grade_id=None, class_id=None):
    level, grade, cls, classes = _pick(scope, grade_id, class_id)
    by_class, taken, expected = _attendance_counts(classes, scope.start, scope.end)
    base = {"year": scope.year.pk, "term": scope.term.pk if scope.term else "year"}
    note = "Attendance rate = (present + late) ÷ days with a register; excused and absent count as not attended."
    tables = []
    if level == "class":
        rows = []
        records = defaultdict(Counter)
        absent_days = defaultdict(list)
        for r in AttendanceRecord.objects.filter(class_group=cls, session__date__range=(scope.start, scope.end)).select_related("session"):
            records[r.learner_id][r.status] += 1
            if r.status == "absent":
                absent_days[r.learner_id].append(r.session.date)
        for e in _enrolled([cls], min(scope.end, timezone.localdate())):
            c = records[e.learner_id]
            total = sum(c.values())
            rows.append([Cell(e.learner.full_name, url=_url("learner", learner=e.learner_id, **base)), e.learner.learner_number,
                         c["present"], c["late"], c["absent"], c["excused"],
                         Cell(pct_text(c["present"] + c["late"], total), tone=_tone(pct(c["present"] + c["late"], total), 90, 80)),
                         ", ".join(f"{d.day} {d:%b}" for d in sorted(absent_days[e.learner_id]))])
        tables.append(Table(f"Class {cls.name}: learners", ["Learner", "Number", "Present", "Late", "Absent", "Excused", "Rate", "Absent on"],
                            rows, note))
        summary = _attendance_row(f"Class {cls.name}", by_class[cls.pk], taken.get(cls.pk, 0), expected.get(cls.pk, 0))
        tables.insert(0, Table("Summary", ATT_COLUMNS, [summary], note))
        return Result(f"Attendance · Class {cls.name}", tables, sensitive=True, level=level,
                      crumbs=_crumbs("attendance", scope, level, grade, cls))
    if level in ("grade", "mine"):
        rows, totals, t_taken, t_expected = [], Counter(), 0, 0
        for c in classes:
            rows.append(_attendance_row(Cell(f"Class {c.name}", url=_url("attendance", cls=c.pk, **base)), by_class[c.pk],
                                        taken.get(c.pk, 0), expected.get(c.pk, 0)))
            totals.update(by_class[c.pk])
            t_taken += taken.get(c.pk, 0)
            t_expected += expected.get(c.pk, 0)
        title = grade.name if level == "grade" else "My classes"
        tables.append(Table(title, ATT_COLUMNS, rows, note, total=_attendance_row("Total", totals, t_taken, t_expected)))
    else:
        for stage, grades in _by_stage(classes):
            rows, totals, t_taken, t_expected = [], Counter(), 0, 0
            for g, cs in grades:
                counts, g_taken, g_expected = Counter(), 0, 0
                for c in cs:
                    counts.update(by_class[c.pk])
                    g_taken += taken.get(c.pk, 0)
                    g_expected += expected.get(c.pk, 0)
                rows.append(_attendance_row(Cell(g.name, url=_url("attendance", grade=g.pk, **base)), counts, g_taken, g_expected))
                totals.update(counts)
                t_taken += g_taken
                t_expected += g_expected
            tables.append(Table(stage.name, ATT_COLUMNS, rows, note, total=_attendance_row(f"All {stage.name}", totals, t_taken, t_expected)))
    return Result("Attendance", tables, level=level, crumbs=_crumbs("attendance", scope, level, grade, cls))


# 2. Absenteeism and follow-up -----------------------------------------------------


def followup(scope, grade_id=None, class_id=None):
    level, grade, cls, classes = _pick(scope, grade_id, class_id)
    base = {"year": scope.year.pk, "term": scope.term.pk if scope.term else "year"}
    cases = list(AbsenceCase.objects.filter(class_group__in=classes).select_related("learner", "class_group__grade"))
    contacts = Counter(FollowUpAction.objects.filter(case__in=cases, kind="contact", contact_date__range=(scope.start, scope.end))
                       .values_list("case__class_group", flat=True))
    today = timezone.localdate()

    def metrics(label, group):
        opened = [c for c in group if scope.start <= timezone.localdate(c.flagged_at) <= scope.end]
        open_now = [c for c in group if c.is_open]
        days_open = [(today - c.first_absent_date).days for c in open_now]
        return [label, len(opened), len(open_now), sum(1 for c in group if c.status == AbsenceCase.ESCALATED),
                sum(1 for c in group if c.resolved_at and scope.start <= timezone.localdate(c.resolved_at) <= scope.end),
                round(sum(days_open) / len(days_open)) if days_open else "—",
                sum(contacts[c.pk] for c in {x.class_group for x in group}) if group else 0]

    columns = ["", "Opened in period", "Open now", "Escalated", "Resolved in period", "Average days open", "Contacts made"]
    tables = []
    if level == "class":
        rows = []
        for c in sorted(cases, key=lambda c: c.last_absent_date, reverse=True):
            last = c.actions.order_by("-created_at").first()
            rows.append([Cell(c.learner.full_name, url=_url("learner", learner=c.learner_id, **base)),
                         f"{c.first_absent_date:%d %b} – {c.last_absent_date:%d %b}", c.days_absent,
                         Cell(c.get_status_display(), tone="bad" if c.status == "escalated" else ""),
                         c.actions.filter(kind="contact").count(), last.created_at.date() if last else ""])
        tables.append(Table(f"Class {cls.name}: cases", ["Learner", "Absent", "Days", "Status", "Contacts", "Last action"], rows))
    elif level in ("grade", "mine"):
        rows = [metrics(Cell(f"Class {c.name}", url=_url("followup", cls=c.pk, **base)), [x for x in cases if x.class_group_id == c.pk])
                for c in classes]
        tables.append(Table(grade.name if grade else "My classes", columns, rows, total=metrics("Total", cases)))
    else:
        for stage, grades in _by_stage(classes):
            rows = [metrics(Cell(g.name, url=_url("followup", grade=g.pk, **base)), [x for x in cases if x.class_group.grade_id == g.pk])
                    for g, _ in grades]
            tables.append(Table(stage.name, columns, rows))
    reasons = Counter(FollowUpAction.objects.filter(case__in=cases, kind="contact").exclude(reason_category="")
                      .values_list("reason_category", flat=True))
    if reasons:
        tables.append(Table("Reasons recorded", ["Reason", "Contacts"],
                            [[r.replace("_", " ").capitalize(), n] for r, n in reasons.most_common()]))
    title = "Absenteeism and follow-up" + (f" · Class {cls.name}" if cls else "")
    return Result(title, tables, sensitive=level == "class", level=level, crumbs=_crumbs("followup", scope, level, grade, cls))


# 3. Academic performance ------------------------------------------------------------


@dataclass
class SubjectStats:
    scores: list = field(default_factory=list)  # (gender, SubjectScore)

    def add(self, gender, score):
        self.scores.append((gender, score))

    def row(self, label):
        done = [s for _, s in self.scores if s.complete]
        passed = [s for s in done if s.band and s.band.is_pass]
        pending = len(self.scores) - len(done)

        def avg(items):
            return round(sum(float(s.percent) for s in items) / len(items), 1) if items else "—"

        girls = [s for g, s in self.scores if g == "F" and s.complete]
        boys = [s for g, s in self.scores if g == "M" and s.complete]
        bands = Counter(s.band.label for s in done if s.band)
        return [label, len(done), avg(done), Cell(pct_text(len(passed), len(done)), tone=_tone(pct(len(passed), len(done)), 75, 50)),
                pending, avg(girls), avg(boys), ", ".join(f"{b} {n}" for b, n in sorted(bands.items()))]


PERF_COLUMNS = ["", "Scored", "Average %", "Pass rate", "Pending", "Girls avg", "Boys avg", "Grades"]
PERF_NOTE = "Pass rate = learners with a passing grade ÷ learners with a score; pending scores are counted separately."


def _class_results(c, term, subject_id=None):
    learners = [e.learner for e in _enrolled([c], min(term.end_date, timezone.localdate()))]
    class_subjects, rows = calc.class_term_results(c, term, learners)
    if subject_id:
        keep = [i for i, cs in enumerate(class_subjects) if str(cs.subject_id) == str(subject_id)]
        class_subjects = [class_subjects[i] for i in keep]
        for r in rows:
            r.subjects = [r.subjects[i] for i in keep]
    return class_subjects, rows


def performance(scope, grade_id=None, class_id=None, subject_id=None):
    level, grade, cls, classes = _pick(scope, grade_id, class_id)
    term = scope.term
    if term is None:
        return Result("Academic performance", [Table("Choose a term", [""], [["Academic performance is reported per term."]])])
    base = {"year": scope.year.pk, "term": term.pk, "subject": subject_id or ""}
    tables = []
    per_class = {c.pk: _class_results(c, term, subject_id) for c in classes}
    schemes = {c.pk: (lambda p: str(p.grading_scheme) if p else "no plan")(calc.plan_for(c, term)) for c in classes}

    def stats_for(cs_list):
        overall, by_subject = SubjectStats(), defaultdict(SubjectStats)
        for c in cs_list:
            class_subjects, rows = per_class[c.pk]
            for r in rows:
                for cs, s in zip(class_subjects, r.subjects):
                    overall.add(r.learner.gender, s)
                    by_subject[cs.subject].add(r.learner.gender, s)
        return overall, by_subject

    if level == "class":
        class_subjects, rows = per_class[cls.pk]
        _, by_subject = stats_for([cls])
        tables.append(Table(f"Class {cls.name}: subjects", ["Subject"] + PERF_COLUMNS[1:],
                            [st.row(subj.name) for subj, st in sorted(by_subject.items(), key=lambda kv: kv[0].name)],
                            f"{PERF_NOTE} Graded with {schemes[cls.pk]}."))
        learner_rows = []
        for r in rows:
            learner_rows.append([Cell(r.learner.full_name, url=_url("learner", learner=r.learner.pk, year=scope.year.pk, term=term.pk))]
                                + [f"{s.percent} {s.band.label if s.band else ''}".strip() if s.complete else Cell("pending", tone="warn")
                                   for s in r.subjects] + [r.overall if r.overall is not None else "pending"])
        tables.append(Table(f"Class {cls.name}: learners", ["Learner"] + [cs.subject.name for cs in class_subjects] + ["Overall"], learner_rows))
        return Result(f"Academic performance · Class {cls.name} · {term}", tables, sensitive=True, level=level,
                      crumbs=_crumbs("performance", scope, level, grade, cls))
    if level in ("grade", "mine"):
        rows = []
        for c in classes:
            overall, _ = stats_for([c])
            rows.append(overall.row(Cell(f"Class {c.name}", url=_url("performance", cls=c.pk, **base))))
        overall, by_subject = stats_for(classes)
        mixed = len({schemes[c.pk] for c in classes}) > 1
        tables.append(Table(grade.name if grade else "My classes", PERF_COLUMNS, rows,
                            PERF_NOTE + (" ⚠ These classes use different grading schemes: compare with care." if mixed else ""),
                            total=overall.row("All") if len(classes) > 1 else None))
        subjects = sorted(by_subject, key=lambda s: s.name)
        matrix = []
        for subj in subjects:
            row = [subj.name]
            for c in classes:
                _, bs = stats_for([c])
                st = bs.get(subj)
                done = [s for _, s in st.scores if s.complete] if st else []
                row.append(round(sum(float(s.percent) for s in done) / len(done), 1) if done else "—")
            matrix.append(row)
        if len(classes) > 1:
            tables.append(Table("Average % by subject and class", ["Subject"] + [f"Class {c.name}" for c in classes], matrix))
        return Result(f"Academic performance · {grade.name if grade else 'My classes'} · {term}", tables, level=level,
                      crumbs=_crumbs("performance", scope, level, grade, cls))
    for stage, grades in _by_stage(classes):
        rows, used = [], set()
        for g, cs in grades:
            overall, _ = stats_for(cs)
            rows.append(overall.row(Cell(g.name, url=_url("performance", grade=g.pk, **base))) + [schemes[cs[0].pk]])
            used.add(schemes[cs[0].pk])
        note = PERF_NOTE + (" ⚠ Grades in this stage use different grading schemes: compare with care." if len(used) > 1 else "")
        tables.append(Table(stage.name, PERF_COLUMNS + ["Grading scheme"], rows, note))
    return Result(f"Academic performance · {term}", tables, level=level, crumbs=_crumbs("performance", scope, level, grade, cls))


# 4. Competency development ----------------------------------------------------------


def competencies(scope, grade_id=None, class_id=None):
    level, grade, cls, classes = _pick(scope, grade_id, class_id)
    term = scope.term
    if term is None:
        return Result("Competency development", [Table("Choose a term", [""], [["Competencies are recorded each term."]])])
    base = {"year": scope.year.pk, "term": term.pk}
    sheets = list(CompetencySheet.objects.filter(class_group__in=classes, term=term, status="submitted").select_related("framework"))
    results = list(LearnerCompetencyResult.objects.filter(sheet__in=sheets).select_related("level", "competency", "learner", "class_group"))
    labels = []
    for s in sheets:
        for lvl in s.framework.levels.order_by("sequence"):
            if lvl.label not in labels:
                labels.append(lvl.label)
    columns = labels + ["Not assessed"]

    def counts(items):
        c = Counter("Not assessed" if r.not_assessed else (r.level.label if r.level else "—") for r in items)
        return [c.get(col, 0) for col in columns]

    tables = []
    note = f"Submitted sheets for {term}. Not assessed is a status, never the lowest level."
    if level == "class":
        mine = [r for r in results if r.class_group_id == cls.pk]
        by_comp = defaultdict(list)
        for r in mine:
            by_comp[r.competency].append(r)
        tables.append(Table(f"Class {cls.name}: competencies", ["Competency"] + columns,
                            [[comp.name] + counts(items) for comp, items in sorted(by_comp.items(), key=lambda kv: kv[0].sequence)], note))
        comps = sorted(by_comp, key=lambda c: c.sequence)
        by_learner = defaultdict(dict)
        for r in mine:
            by_learner[r.learner][r.competency_id] = "Not assessed" if r.not_assessed else (r.level.label if r.level else "—")
        tables.append(Table(f"Class {cls.name}: learners", ["Learner"] + [c.name for c in comps],
                            [[Cell(l.full_name, url=_url("learner", learner=l.pk, **base))] + [cells.get(c.pk, "—") for c in comps]
                             for l, cells in sorted(by_learner.items(), key=lambda kv: (kv[0].last_name, kv[0].first_name))]))
        return Result(f"Competency development · Class {cls.name} · {term}", tables, sensitive=True, level=level,
                      crumbs=_crumbs("competencies", scope, level, grade, cls))
    submitted = {s.class_group_id for s in sheets}
    if level in ("grade", "mine"):
        rows = [[Cell(f"Class {c.name}", url=_url("competencies", cls=c.pk, **base)), "yes" if c.pk in submitted else Cell("not submitted", tone="warn")]
                + counts([r for r in results if r.class_group_id == c.pk]) for c in classes]
        tables.append(Table(grade.name if grade else "My classes", ["", "Sheet"] + columns, rows, note))
    else:
        for stage, grades in _by_stage(classes):
            rows = [[Cell(g.name, url=_url("competencies", grade=g.pk, **base)), f"{sum(1 for c in cs if c.pk in submitted)} / {len(cs)}"]
                    + counts([r for r in results if r.class_group.grade_id == g.pk]) for g, cs in grades]
            tables.append(Table(stage.name, ["", "Sheets submitted"] + columns, rows, note))
    return Result(f"Competency development · {term}", tables, level=level, crumbs=_crumbs("competencies", scope, level, grade, cls))


# 5. Enrollment and class lists ---------------------------------------------------------


def can_see_contacts(user, class_group):
    """Decision R4: guardian phones on class lists for office roles and the class teacher only."""
    return sees_whole_school(user) or is_class_teacher(user, class_group, timezone.localdate())


def enrollment(scope, grade_id=None, class_id=None):
    level, grade, cls, classes = _pick(scope, grade_id, class_id)
    day = min(scope.end, timezone.localdate())
    base = {"year": scope.year.pk, "term": scope.term.pk if scope.term else "year"}
    enrolled = _enrolled(classes, day)
    tables = []

    def gender_row(label, items):
        g = Counter(e.learner.gender or "?" for e in items)
        return [label, g.get("F", 0), g.get("M", 0), g.get("?", 0), len(items)]

    columns = ["", "Girls", "Boys", "Not recorded", "Total"]
    if level == "class":
        contacts = can_see_contacts(scope.user, cls)
        rows = []
        for e in [x for x in enrolled if x.class_group_id == cls.pk]:
            learner = e.learner
            row = [learner.learner_number, Cell(learner.full_name, url=_url("learner", learner=learner.pk, **base)),
                   learner.get_gender_display(), learner.date_of_birth or ""]
            if contacts:
                link = learner.guardian_links.filter(status="active", contact_role="primary").select_related("guardian").first()
                contact = link.guardian.current_contact() if link else None
                row += [link.guardian.full_name if link else "—", contact.phone if contact else "—"]
            rows.append(row)
        heads = ["Number", "Learner", "Gender", "Date of birth"] + (["Primary guardian", "Mobile"] if contacts else [])
        tables.append(Table(f"Class list {cls.name} ({cls.academic_year})", heads, rows,
                            f"Enrolled on {day:%d %b %Y}." + ("" if contacts else " Guardian contacts are shown to the class teacher and the office.")))
        return Result(f"Class list · {cls.name}", tables, sensitive=True, level=level, crumbs=_crumbs("enrollment", scope, level, grade, cls))
    if level in ("grade", "mine"):
        rows = [gender_row(Cell(f"Class {c.name}", url=_url("enrollment", cls=c.pk, **base)), [e for e in enrolled if e.class_group_id == c.pk])
                for c in classes]
        tables.append(Table(grade.name if grade else "My classes", columns, rows, f"Enrolled on {day:%d %b %Y}.",
                            total=gender_row("Total", enrolled) if len(classes) > 1 else None))
    else:
        for stage, grades in _by_stage(classes):
            rows = [gender_row(Cell(g.name, url=_url("enrollment", grade=g.pk, **base)), [e for e in enrolled if e.class_group.grade_id == g.pk])
                    for g, _ in grades]
            stage_items = [e for e in enrolled if e.class_group.grade.stage_id == stage.pk]
            tables.append(Table(stage.name, columns, rows, f"Enrolled on {day:%d %b %Y}.", total=gender_row(f"All {stage.name}", stage_items)))
        period = Enrollment.objects.filter(school=scope.user.school).exclude(status="reversed")
        new = period.filter(start_date__range=(scope.start, scope.end), previous_enrollment__isnull=True).count()
        transfers = period.filter(start_date__range=(scope.start, scope.end), request__kind=EnrollmentRequest.TRANSFER).count()
        promoted = period.filter(start_date__range=(scope.start, scope.end), request__kind=EnrollmentRequest.PROMOTION).count()
        left = period.filter(end_date__range=(scope.start, scope.end), end_reason__in=["withdrawal", "completion"]).count()
        tables.append(Table("Movements in the period", ["Movement", "Learners"],
                            [["New registrations", new], ["Class transfers", transfers], ["Promoted or repeating (new year)", promoted],
                             ["Left or completed", left]]))
    return Result("Enrollment", tables, level=level, crumbs=_crumbs("enrollment", scope, level, grade, cls))


# 6. Individual learner report ---------------------------------------------------------


def learner_report(user, learner):
    """Profile, enrollment, attendance, follow-up, results, competencies and trends (§11)."""
    tables = []
    current = learner.current_enrollment()
    profile = [["Learner number", learner.learner_number], ["Name", learner.full_name], ["Gender", learner.get_gender_display()],
               ["Date of birth", learner.date_of_birth or "—"], ["Status", learner.status.capitalize()],
               ["Current class", f"{current.class_group.name} ({current.academic_year})" if current else "—"],
               ["Registered", learner.registration_date]]
    for link in learner.active_links():
        contact = link.guardian.current_contact()
        profile.append([f"Guardian ({link.contact_role})" if link.contact_role != "none" else "Guardian",
                        f"{link.guardian.full_name}, {link.relationship or 'guardian'}" + (f", {contact.phone}" if contact else "")])
    tables.append(Table("Profile", ["", ""], profile))
    tables.append(Table("Enrollment history", ["Year", "Class", "From", "To", "Status", "Reason"],
                        [[e.academic_year.name, e.class_group.name, e.start_date, e.end_date or "", e.status, e.end_reason]
                         for e in learner.enrollments.select_related("academic_year", "class_group").order_by("start_date")]))

    att_rows = []
    for year in AcademicYear.objects.filter(enrollments__learner=learner).distinct().order_by("start_date"):
        recs = list(AttendanceRecord.objects.filter(learner=learner, session__date__range=(year.start_date, year.end_date))
                    .select_related("session"))
        c = Counter(r.status for r in recs)
        absent = sorted(r.session.date for r in recs if r.status == "absent")
        att_rows.append([year.name, len(recs), c["present"], c["late"], c["absent"], c["excused"],
                         pct_text(c["present"] + c["late"], len(recs)), ", ".join(f"{d.day} {d:%b}" for d in absent)])
    tables.append(Table("Attendance", ["Year", "Days recorded", "Present", "Late", "Absent", "Excused", "Rate", "Absent on"], att_rows,
                        "Rate = (present + late) ÷ days with a register."))

    case_rows = []
    for case in learner.absence_cases.order_by("first_absent_date"):
        for a in case.actions.select_related("created_by").order_by("created_at"):
            case_rows.append([f"{case.first_absent_date:%d %b %Y}", case.get_status_display(), a.get_kind_display(),
                              a.created_at.date(), (a.outcome or "")[:200], str(a.created_by or "system")])
    tables.append(Table("Absence follow-up", ["Case from", "Case status", "Action", "Date", "Details", "By"], case_rows))

    result_rows, type_rows, overall_by_term = [], [], []
    terms = Term.objects.filter(assessments__results__learner=learner, assessments__status="submitted").distinct().order_by("start_date")
    for term in terms:
        class_subjects = {r.assessment.class_subject for r in learner.assessment_results.filter(
            assessment__term=term, assessment__status="submitted").select_related("assessment__class_subject__subject",
                                                                                   "assessment__class_subject__class_group__grade")}
        scores = []
        for cs in sorted(class_subjects, key=lambda c: c.subject.name):
            s = calc.subject_score(learner, cs, term)
            scores.append(s)
            result_rows.append([str(term), cs.subject.name, f"{s.percent}%" if s.complete else "pending",
                                s.band.label if s.band else "", s.pending])
        if scores and all(s.complete for s in scores):
            overall_by_term.append((term, round(sum(float(s.percent) for s in scores) / len(scores), 1)))
        by_type = defaultdict(list)
        for r in learner.assessment_results.filter(assessment__term=term, assessment__status="submitted", score__isnull=False) \
                .select_related("assessment__assessment_type", "assessment__class_subject__subject"):
            by_type[(r.assessment.class_subject.subject.name, r.assessment.assessment_type.name)].append(
                float(r.score) / float(r.assessment.max_score) * 100)
        for (subject, kind), values in sorted(by_type.items()):
            type_rows.append([str(term), subject, kind, round(sum(values) / len(values), 1), len(values)])
    tables.append(Table("Results by subject and term", ["Term", "Subject", "Score", "Grade", "Pending because"], result_rows))
    tables.append(Table("Results by assessment type", ["Term", "Subject", "Type", "Average %", "Assessments"], type_rows))
    trend = []
    previous = None
    for term, overall in overall_by_term:
        change = "" if previous is None else ("▲" if overall > previous else ("▼" if overall < previous else "="))
        trend.append([str(term), overall, Cell(change, tone="good" if change == "▲" else ("bad" if change == "▼" else ""))])
        previous = overall
    tables.append(Table("Overall score trend", ["Term", "Overall %", "Change"], trend,
                        "Overall = average of subject scores, when every subject has one."))

    comp_rows = [[str(r.sheet.term), r.competency.name, "Not assessed" if r.not_assessed else (r.level.label if r.level else "—"), r.comment]
                 for r in learner.competency_results.filter(sheet__status="submitted").select_related("sheet__term", "competency", "level")
                 .order_by("sheet__term__start_date", "competency__sequence")]
    tables.append(Table("Competency development", ["Term", "Competency", "Level", "Comment"], comp_rows))
    tables.append(Table("Report cards", [""], [["Approved report cards appear here once report cards are in use (slice 4)."]]))
    return Result(f"Learner report · {learner.full_name}", tables, sensitive=True, level="learner")


REPORTS = {
    "attendance": ("Attendance", "Registers taken, attendance rates and absence dates", attendance),
    "followup": ("Absenteeism and follow-up", "Absence cases, escalations, contacts and reasons", followup),
    "performance": ("Academic performance", "Subject averages, pass rates and grades, by class and grade", performance),
    "competencies": ("Competency development", "Levels reached in each competency", competencies),
    "enrollment": ("Enrollment and class lists", "Learners per class by gender, class lists and movements", enrollment),
}


def subjects_for(scope):
    return Subject.objects.filter(class_subjects__class_group__in=scope.classes).distinct().order_by("name")


def grades_for(scope):
    return Grade.objects.filter(classes__in=scope.classes).distinct().order_by("sequence")


__all__ = ["REPORTS", "Scope", "Result", "build_scope", "learner_report", "ClassGroup"]
