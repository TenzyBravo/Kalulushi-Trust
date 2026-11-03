# Design — Reports, Comparisons and Exports

**Status:** Approved 3 November 2026 — R1–R6 accepted as recommended\
**Version:** 1.0\
**Date:** 3 November 2026\
**Implements:** Requirements baseline v1.1, §11 (report types, drill-down and filters, individual learner
report, comparisons, exports and access), §13 `report_exports`, §15 (sensitive exports audited)\
**Builds on:** attendance and absence follow-up (slice 2), marks, grades and competencies (slice 3),
enrollments and promotions, the access rules in `staffing.access`, and the audit log. The role-specific
dashboard (§11 "Dashboard") already exists.

## 1. Principles

- **Calculated from source records.** Every figure comes from registers, marks, competency sheets and
  enrollments at the moment the report is opened. No totals are stored (§11), so a correction shows up
  in every report at once.
- **Same access as the screens.** A report or export shows only what the user could see on screen:
  office roles see the whole school; a teacher sees the classes they teach or lead (§11).
- **Compare only like with like.** Grades 1–7 and Forms 1–4 are never compared with each other.
  Classes are compared on performance only where they used the same assessment plan and grading-scheme
  version; where they differ, the report says so (§11).
- **Every export is recorded.** Exports with learner-level personal data are sensitive and audited (§15).

## 2. Reports

All reports drill down: **whole school → grade (within its stage) → class → learner**, each level a
link to the next. Filters: academic year, term or date range, grade, class and subject, as relevant.

| # | Report | Shows | Levels |
|---|---|---|---|
| 1 | **Attendance** | School days, registers taken vs expected, present / late / absent / excused, attendance rate (R5), by gender (R3); a learner's absence dates | school, grade, class, learner |
| 2 | **Absenteeism and follow-up** | Cases opened, open, escalated, resolved; days open; reasons recorded; contacts made; long-outstanding cases | school, grade, class, learner |
| 3 | **Academic performance** | Per subject: average score, pass rate (R6), grade-band distribution, pending count; by gender; class and grade comparisons within a stage | school, grade, class, subject, learner |
| 4 | **Competency development** | Per competency: learners at each level, Not Assessed count | school, grade, class, learner |
| 5 | **Enrollment and class lists** | Learners per class and grade by gender; class lists (number, name, gender, date of birth, primary guardian and phone per R4); movements (new, transferred, promoted, repeating, left) for a period | school, grade, class |
| 6 | **Individual learner report** | Profile and enrollment history; attendance rate, statuses and absence dates; absence cases and every follow-up action; results by subject, assessment type, term and year with trends; competencies by term; approved report cards (after slice 4) | learner |
| 7 | **Report-card status** | Comes with slice 4 (report cards); listed now so the menu is complete | — |

**Trends** in the learner report and class comparisons are shown as small term-by-term tables with
up / down markers; charts can follow later.

## 3. Exports

| Format | How |
|---|---|
| **Excel** | Every report downloads as `.xlsx`: one sheet per table, with the filters written at the top. |
| **Print-ready** | Every report has a print view (A4, school name, filters, date printed, page numbers). |
| **PDF** | Decision R1. |

### `report_exports`

One row per export, written in the same transaction as the audit record.

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `user_id` | FK → `users` | |
| `report` | text | e.g. `attendance`, `class_list`, `learner` |
| `format` | text | `xlsx` \| `print` \| `pdf` |
| `filters` | jsonb | year, term, dates, grade, class, subject, learner |
| `row_count` | integer | |
| `sensitive` | boolean | learner-level personal data (R2) |
| `created_at` | timestamptz | |

Append-only (trigger), like the audit log. A sensitive export also writes `report.export` to the audit
log. Support notes never appear in any report or export (slice 1 rule).

## 4. Screens

1. **Reports** (header link): the report types as cards, each opening at the user's widest level
   (school for office roles, their classes for teachers).
2. **Report page**: filter bar, the table for the current level with links one level down, a
   comparison table where relevant, **Download Excel** and **Print** buttons, and a note under any
   comparison that mixes plans or grading schemes.
3. **Learner report**: also reachable from each learner's profile (**Full report** button).
4. **Exports log** (System Administrator and Headteacher): who exported what, when, with which filters.

## 5. Permissions

| | Sys Admin | Headteacher | Academic Admin | Teacher |
|---|---|---|---|---|
| Reports 1–5 | whole school | whole school | whole school | classes they teach or lead |
| Learner report | all learners | all learners | all learners | learners they can see |
| Export | as on screen | as on screen | as on screen | as on screen |
| Exports log | yes | yes | — | — |

## 6. Decisions

| # | Question | Recommendation |
|---|---|---|
| R1 | **PDF exports.** Generate PDFs on the server now, or use the print view (the browser's *Save as PDF*) until report cards need server PDFs? | **Print view now** (A4 layout, works on any device, no extra software on the server); add one-click server PDF in slice 4, when report cards need it, and use the same engine for reports. |
| R2 | Which exports are **sensitive** (audited)? | **Any export with learner-level data** (names with numbers, dates of birth, guardian phones, absence details, marks per learner). Summary-only exports (counts and percentages) are still logged in the exports log, but not flagged. |
| R3 | **Gender breakdowns** (F / M) in attendance, enrollment and performance? | **Yes**, as extra columns and totals: commonly asked for by the Ministry and funders. |
| R4 | **Guardian phone numbers on class lists** | **Office roles and the class teacher only**; subject teachers get the list without contacts. |
| R5 | **Attendance rate** | **(Present + Late) ÷ days with a register**; Excused and Absent are both shown separately and count as not attended. (The alternative leaves Excused out of the count altogether.) |
| R6 | **Pass rate** | **Learners whose subject score is in a pass band** of the grading scheme ÷ learners with a score; learners whose score is pending are shown separately and left out. |

## 7. Next steps

1. Build: report queries from source records, the seven report pages (report-card status as a
   placeholder), Excel and print exports, the exports log and trigger, the learner report, tests that
   reports match the screens and respect access.
