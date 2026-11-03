# Database Design — Slice 3: Curriculum, Assessments and Competencies

**Status:** Draft for review — decisions B1–B8 need answers\
**Version:** 0.1\
**Date:** 3 November 2026\
**Implements:** Requirements baseline v1.1, §2 (curriculum, versioning), §7 (assessment entry, drafts and
submission, missing assessments, competency assessment), §8 (grading, calculations, optional rankings),
§13 "Assessment and competency"\
**Builds on:** [Slice 1](database-design-foundation.md) (stages, grades, terms, classes per year,
class subjects, teacher assignments and cover with `assessment_entry` and `competency_entry` duties,
enrollments, audit log) and [Slice 2](database-design-attendance.md) (composite-key pattern for records
tied to a class and enrollment)\
**Assumed stack:** PostgreSQL 16 with Django 5.2. Rules are stated as database rules first.

## 1. Scope

| Area | Tables |
|---|---|
| Grading | `grading_schemes`, `grade_bands` |
| Assessment structure | `assessment_types`, `assessment_plans`, `assessment_components` |
| Marks | `assessments`, `assessment_results`, `assessment_result_history` |
| Competencies | `competency_frameworks`, `competencies`, `competency_levels`, `competency_sheets`, `learner_competency_results` |

Out of scope: report cards and their snapshots of results (slice 4); SMS of results (slice 5); reports,
comparisons and exports (slice 6); specialised examination-class calculations (Phase 2, baseline §2).

## 2. How a term works

1. Before the term, the **Academic Administrator** checks the **assessment plan** for each grade (or
   stage): its components and weights, e.g. *Continuous assessment 40% + End-of-term exam 60%*, and
   which **grading scheme** version turns percentages into grades.
2. During the term, a **subject teacher** creates assessments for their class subject, e.g. "Test 1"
   (out of 25) under *Continuous assessment*, and enters marks in a class table. Each learner gets a
   mark or a **missing status** (Absent, Excused, Not Yet Assessed); a missing result is never zero.
3. Marks save as a **draft** automatically (Saving / Saved / Save failed) and with **Save draft**. The
   teacher **submits** the whole class once every learner has a mark or a missing status.
4. After submission, a change is a **correction**: the teacher may correct their own class subject
   with a reason; the old and new values go to the result history and the audit log.
5. At the **end of term**, competencies are recorded (decision B6 decides by whom) in a table of
   learners × competencies with level dropdowns; missing ones are **Not Assessed**, never the lowest level.
6. The system calculates subject term scores, grades and the overall term score from the plan and
   scheme in force. A learner with an unresolved required assessment gets no subject score, overall
   score or position (§8). Report cards (slice 4) take a snapshot of these.

## 3. Grading schemes

### `grading_schemes`

Versioned: an active scheme is never edited; a change is a new version (baseline §2).

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `stage_id` | FK → `education_stages` | Primary and Secondary have separate schemes (never compared, §2) |
| `name` | text | e.g. `Secondary percentage grades` |
| `version` | smallint | `UNIQUE (school_id, stage_id, name, version)` |
| `effective_from_year_id` | FK → `academic_years` | first year it may be used |
| `status` | text | `draft` \| `active` \| `retired` |
| `decimal_places` | smallint | rounding of percentages, `CHECK (decimal_places BETWEEN 0 AND 2)`; half-up |
| `missing_rule` | text | `block` \| `exclude_excused` (decision B4) |
| `created_by`, `activated_by`, `activated_at` | | |

### `grade_bands`

| Column | Type | Rules |
|---|---|---|
| `scheme_id` | FK | |
| `label` | text | e.g. `Distinction`, `1`, `A` |
| `description` | text | optional, shown on report cards |
| `min_percent`, `max_percent` | numeric(5,2) | `CHECK (0 <= min_percent <= max_percent <= 100)` |
| `points` | smallint | optional (for points-based schemes) |
| `is_pass` | boolean | |
| | | no overlapping bands: `EXCLUDE USING gist (scheme_id WITH =, numrange(min_percent, max_percent, '[]') WITH &&)` |

Bands must cover 0–100 with no gaps at the scheme's rounding; that is checked when a scheme is
activated (it spans rows). A trigger refuses `INSERT`, `UPDATE` or `DELETE` on bands of a scheme that
is not `draft`, so the rules behind recorded results can never change underneath them.

## 4. Assessment structure

### `assessment_types`

A short list of kinds: `Test`, `Assignment`, `Project`, `Mid-term exam`, `End-of-term exam`.
`UNIQUE (school_id, name)`, `status` `active` \| `retired`.

### `assessment_plans`

Which components, weights and grading scheme apply to a grade (or a whole stage) in a term.

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `academic_year_id` | FK | |
| `term_id` | FK → `terms` | |
| `stage_id` | FK | the stage it covers … |
| `grade_id` | FK, null | … or only this grade, which then overrides the stage plan |
| `grading_scheme_id` | FK | must be `active` and for the same stage |
| `status` | text | `draft` \| `active` \| `locked` (locked once any assessment under it is submitted) |
| | | one plan per stage or grade per term: `UNIQUE (term_id, stage_id) WHERE grade_id IS NULL`, `UNIQUE (term_id, grade_id) WHERE grade_id IS NOT NULL` |

### `assessment_components`

| Column | Type | Rules |
|---|---|---|
| `plan_id` | FK | |
| `name` | text | e.g. `Continuous assessment`, `End-of-term exam` |
| `assessment_type_ids` | | which types count towards it (link table) |
| `weight` | numeric(5,2) | `CHECK (weight > 0 AND weight <= 100)` |
| `required` | boolean | a required component missing blocks the subject score |
| `sequence` | smallint | |
| | | weights of a plan add up to 100: checked when the plan is activated |

Several assessments may fall under one component (Test 1, Test 2 …); how they combine is decision B3.

## 5. Marks

### `assessments`

One assessment of one class subject, e.g. "5A Mathematics — Test 1".

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `class_subject_id` | FK → `class_subjects` | |
| `term_id` | FK | must be a term of the class's year (application check) |
| `component_id` | FK → `assessment_components` | from the plan in force for this class's grade and term |
| `assessment_type_id` | FK | |
| `title` | text | `UNIQUE (class_subject_id, term_id, title)` |
| `max_score` | numeric(6,2) | `CHECK (max_score > 0)` |
| `assessed_on` | date | |
| `status` | text | `draft` \| `submitted` |
| `created_by`, `submitted_by`, `submitted_at` | | `CHECK`: submitted fields set if and only if `status = 'submitted'` |
| | | `UNIQUE (id, class_subject_id)`: target for results |

### `assessment_results`

| Column | Type | Rules |
|---|---|---|
| `assessment_id` | FK | |
| `class_subject_id` | FK | composite `FOREIGN KEY (assessment_id, class_subject_id) REFERENCES assessments (id, class_subject_id)` |
| `class_group_id` | FK | must be the class subject's class (trigger, like attendance) |
| `enrollment_id`, `learner_id` | FK | composite `FOREIGN KEY (enrollment_id, class_group_id, learner_id) REFERENCES enrollments (id, class_id, learner_id)`, the key slice 2 added |
| `score` | numeric(6,2), null | |
| `missing_status` | text, null | `absent` \| `excused` \| `not_yet_assessed` |
| `comment` | text | individual comments only; there is no class-level comment (§7) |
| `updated_by`, `updated_at` | | |
| | | **a mark or a missing status, never both:** `CHECK ((score IS NULL) <> (missing_status IS NULL))` for submitted rows; a draft row may have neither |
| | | `CHECK (score >= 0)`; `score <= max_score` is checked by a trigger against the assessment |
| | | `UNIQUE (assessment_id, learner_id)` |

**Submission** (one action for the whole class): refused unless every learner enrolled in the class on
the assessment date has a row with a score or a missing status. Then the assessment becomes
`submitted` and its plan `locked`. Draft saves are frequent and not individually audited; the
submission is.

### `assessment_result_history`

Append-only (trigger refuses `UPDATE`/`DELETE`). One row per field change **after submission**.

| Column | Type | Rules |
|---|---|---|
| `result_id` | FK | |
| `old_score`, `new_score` | numeric | |
| `old_missing_status`, `new_missing_status` | text | |
| `old_comment`, `new_comment` | text | |
| `reason` | text | required |
| `changed_by`, `changed_at` | | |

A correction writes the result, a history row and an audit record in one transaction. Once a report
card that used the result is approved (slice 4), the correction also starts that card's revision
workflow; until slice 4 exists, corrections are open to the teacher (decision B8).

## 6. Calculations (§8)

All calculations are functions of stored marks, the plan and the scheme; nothing calculated is stored
in this slice (report cards in slice 4 store a snapshot with the scheme version).

For one learner, class subject and term:

1. Each assessment gives a **percentage**: score ÷ max_score × 100.
2. Each **component** combines its assessments (decision B3).
3. The **subject term score** is the weighted sum of component percentages, rounded to the scheme's
   decimal places (half-up), and gets the **grade band** it falls in.
4. **Missing results** (decision B4): `absent` and `not_yet_assessed` in a required component leave
   the subject score **pending**; under `exclude_excused`, an `excused` assessment is left out of its
   component if the component has at least one other result. Nothing missing is ever counted as zero.
5. The **overall term score** (decision B5) is calculated only when every subject score is complete.

**Rankings** (optional, off by default): a new setting `rankings_enabled` in `school_settings`,
changed only by a System Administrator and audited. When on: class position by overall term score,
grade position across parallel classes only where the plan and scheme are the same, subject positions
separately; ties use competition ranking (1, 2, 2, 4). Learners with a pending score get no position.
Approved report cards keep the positions they were approved with.

## 7. Competencies

### `competency_frameworks`

Versioned like grading schemes: `school_id`, `stage_id`, `name`, `version`, `effective_from_year_id`,
`status` (`draft` \| `active` \| `retired`). Competencies and levels of a non-draft framework cannot
change (trigger).

### `competencies`

`framework_id`, `code`, `name`, `description`, `sequence`, and `subject_id` (null for a cross-cutting
competency; set if the competency belongs to one subject, decision B6). `UNIQUE (framework_id, code)`.

### `competency_levels`

`framework_id`, `code`, `label`, `description`, `sequence` (1 = lowest). `UNIQUE (framework_id, sequence)`.
"Not Assessed" is **not** a level; it is a status on the result.

### `competency_sheets`

The end-of-term sheet for one class (or class subject, decision B6) and term: `class_group_id`,
`class_subject_id` (null for cross-cutting), `term_id`, `framework_id`, `status` (`draft` \| `submitted`),
`submitted_by`, `submitted_at`. `UNIQUE (class_group_id, term_id, class_subject_id)`.

### `learner_competency_results`

| Column | Type | Rules |
|---|---|---|
| `sheet_id` | FK | |
| `competency_id` | FK | must belong to the sheet's framework (application check) |
| `enrollment_id`, `learner_id`, `class_group_id` | FK | same composite key to `enrollments` as marks |
| `level_id` | FK → `competency_levels`, null | |
| `not_assessed` | boolean | `CHECK ((level_id IS NULL) = not_assessed)` once the sheet is submitted |
| `comment` | text | |
| `updated_by`, `updated_at` | | |
| | | `UNIQUE (sheet_id, learner_id, competency_id)` |

Submitted corrections need a reason and are audited. Sheets with Not Assessed entries are flagged on
the "incomplete" list and before report-card approval (§7).

## 8. Permissions and access

| Action | Sys Admin | Headteacher | Academic Admin | Teacher |
|---|---|---|---|---|
| Grading schemes, competency frameworks, plans, assessment types | manage | view | manage | view |
| Create assessments and enter marks | — | — | — | own class subjects, or cover with `assessment_entry` |
| Submit a class's marks | — | — | — | same |
| Correct submitted marks, with a reason | yes | yes | yes | own class subjects (B8) |
| Record competencies | — | — | — | per B6, or cover with `competency_entry` |
| View results | all | all | all | classes they teach |
| Missing / incomplete assessments list | all | all | all | their own |
| Turn rankings on or off | yes | — | — | — |

Teacher access reuses slice 1's single access function: primary teacher assignments plus cover in force
with the matching duty. A backup assignment grants nothing by itself.

## 9. Screens

1. **Assessment setup** (Academic Administrator): grading schemes and their bands (draft → activate →
   new version), assessment types, assessment plans per term and grade/stage with weights, competency
   frameworks with competencies and levels.
2. **My subjects** (teacher): the class subjects they teach this term, each with its assessments and
   their status.
3. **Mark entry** (class table): learners as rows; score, missing status and comment per learner;
   auto-save with Saving / Saved / Save failed, **Save draft**, and **Submit** with errors listed first.
   Works on a phone. A **learner view** of the same draft for detailed comments and corrections.
4. **Competency entry** (end of term): learners as rows, competencies as columns, level dropdowns;
   individual learner view for comments.
5. **Missing and incomplete assessments**: by class, subject and term, for teachers (theirs) and the
   office (all).
6. **Learner profile → Academic results** and **Competency development** tabs (placeholders since
   slice 1): term by term, with the scheme version used.

## 10. Decisions

| # | Question | Recommendation |
|---|---|---|
| B1 | **Grade bands.** What grades does each stage use? | Start both stages on a percentage scheme the school can edit: **Distinction 75–100, Merit 60–74, Credit 50–59, Pass 40–49, Fail 0–39**, 0 decimal places. If the school uses ECZ points (1–9) or its own bands for Forms 1–4, give them and we load those instead. |
| B2 | **Term weighting.** How do continuous assessment and the end-of-term exam combine? | **Continuous assessment 40%, end-of-term exam 60%**, both required, set per stage and changeable per grade and term. |
| B3 | Several assessments in one component (Test 1, Test 2): how do they combine? | **Average of their percentages**, so a test out of 20 and one out of 50 count equally. The alternative is total marks ÷ total possible. |
| B4 | **Missing results.** When does a missing mark block the subject score? | **Absent and Not Yet Assessed block it; Excused is left out** if the component has another result. Nothing is ever counted as zero. The Headteacher's exception on a report card comes in slice 4. |
| B5 | **Overall term score.** | **Average of the learner's subject scores**, only when every subject has a score; otherwise pending. |
| B6 | **Competencies.** Are they cross-cutting (one set per class, recorded by the class teacher) or per subject (recorded by each subject teacher)? And what are the official level labels? | **Cross-cutting, recorded by the class teacher**, with the national curriculum's competencies and **four levels** loaded as a framework the school can edit. Please send the official competency list and level labels the school uses. |
| B7 | Who creates assessments? | **The subject teacher**, under the components the Academic Administrator has set; the office can view them all. |
| B8 | How long may a teacher correct submitted marks? | **Until the report card is approved** (slice 4), always with a reason and audited; after that, through the report-card revision workflow. |

## 11. Next steps

1. Answer B1–B8, and send the competency list and level labels for B6.
2. Build slice 3: migrations and triggers, setup screens, mark entry with auto-save, competency entry,
   calculations, missing-assessment list, profile tabs, tests for every database rule and calculation.
3. Design slice 4 (report cards) while slice 3 is built.
