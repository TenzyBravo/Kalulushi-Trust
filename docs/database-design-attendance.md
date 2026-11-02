# Database Design — Slice 2: Calendar, Attendance and Absence Follow-up

**Status:** Approved — decisions A1–A7 answered 2 November 2026, with grade and class register closures added (§3.1)\
**Version:** 1.0\
**Date:** 2 November 2026\
**Implements:** Requirements baseline v1.1, §6 (attendance, school calendar, absence early warning,
absenteeism follow-up screen), §12 screens 6–7, §13 "Attendance and follow-up"\
**Builds on:** [Slice 1: Foundation](database-design-foundation.md) (classes per year, enrollments,
teacher and class-teacher assignments, cover with an `attendance` duty, audit log)\
**Assumed stack:** PostgreSQL 16 with Django 5.2. As in slice 1, every rule is stated as a database
rule first.

## 1. Scope

| Area | Tables |
|---|---|
| School calendar | `calendar_events` (school-wide, grade or class) |
| Daily attendance | `attendance_sessions`, `attendance_records` |
| Absence follow-up | `absence_cases`, `absence_case_records`, `follow_up_actions` |

Slice 1 conventions apply unchanged: `bigint` identity keys, `school_id` on top-level tables, no
routine deletion, `text` statuses with `CHECK` constraints, `timestamptz` stored in UTC and shown
in Africa/Lusaka, date-range rules as exclusion constraints, every change audited in the same
transaction.

Out of scope: per-lesson (period) attendance, which the baseline rules out; SMS to parents about
absence, which the baseline forbids; attendance percentages on report cards (slice 4); attendance
reports and exports (slice 6).

## 2. How a day works

1. The class teacher opens **My classes**, picks their class, and sees today's register. The date can be
   changed to fill in a missed day (§5.3 limits how far back).
2. A new register starts with **nobody marked** (decision A3). **Mark all present** marks everyone
   Present; the teacher then taps the exceptions: Absent, Late or Excused, with an optional note.
3. **Save** writes the register. Saving is refused while anyone is unmarked, so a register can
   never be half-taken.
4. On save, the system checks each learner marked Absent. If they were also Absent on the
   previous school day for which their class has a register, an **absence case** opens (or the open
   one is extended) and appears for the class teacher.
5. The class teacher contacts the family and records each action on the case. Unresolved cases
   can be referred to the Headteacher.

## 3. School calendar

### `calendar_events`

The calendar stores only exceptions. Terms already say when school is in session.

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `academic_year_id` | FK | |
| `kind` | text | `holiday` \| `closure` \| `extra_school_day` |
| `grade_id` | FK → `grades`, null | set for a closure that applies to one grade only (§3.1) |
| `class_group_id` | FK → `classes`, null | set for a closure that applies to one class only (§3.1) |
| `name` | text | required, e.g. `Heroes Day`, `Water cut — school closed`, `Saturday catch-up` |
| `start_date`, `end_date` | date | `CHECK (end_date >= start_date)`; one-day events have equal dates |
| `reason` | text | required for `closure` |
| `created_by` | FK → `users` | |
| `created_at`, `updated_at` | timestamptz | |
| | | `CHECK (grade_id IS NULL OR class_group_id IS NULL)`: a closure is school-wide, for a grade, or for a class |
| | | `CHECK (kind = 'closure' OR (grade_id IS NULL AND class_group_id IS NULL))`: holidays and extra school days are always school-wide |
| | | `end_date` may be null only for a grade or class closure, meaning "until the end of the term": `CHECK (end_date IS NOT NULL OR grade_id IS NOT NULL OR class_group_id IS NOT NULL)` |

**A school day** is a date that:

- falls inside a term of its academic year, **and**
- is either a weekday (Monday–Friday) not covered by a `holiday` or `closure`, **or** is covered by
  an `extra_school_day`.

A **register day for a class** is a school day not covered by a closure for that class's grade or
for the class itself.

These are two functions, `calendar.is_school_day(date)` and `calendar.is_register_day(date,
class_group)`, used by the register, the absence check and
later reports. Events must fall inside their academic year; that spans two tables, so it is
checked by the application and covered by tests. A seed command adds Zambia's fixed public
holidays for a year; movable ones (Easter) are entered in School setup.

### 3.1 Closing a grade's or class's register early

Exam classes often finish before the rest of the school, especially in Term 3: Grade 7 and Form 4
may close after their final exam while other grades keep coming. Staff record this as a
**closure for that grade or class**, usually from the day after the last exam with no end date
("until the end of the term").

From the closure's start date, for that grade or class only:

- no register can be taken (the screen says the register is closed and why);
- those days do not count towards absence runs (§5.1);
- the class does not appear on the "registers not taken" list;
- registers taken before the closure are unchanged.

Other grades and classes are unaffected. A closure can be ended early by setting its end date, for
example if a class is recalled. Creating, changing or ending a closure is audited and allowed only
to the roles in decision A6. Every register date falls in one term, and an open-ended closure lasts
only to the end of its term, so a Term 3 closure never carries into the next year.

## 4. Daily attendance

### `attendance_sessions`

One register per class per date.

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `class_group_id` | FK → `classes` | |
| `academic_year_id` | FK | composite `FOREIGN KEY (class_group_id, academic_year_id) REFERENCES classes (id, academic_year_id)`, reusing slice 1's target |
| `date` | date | must be a school day inside the year (application check) |
| `taken_by` | FK → `users` | who first saved it |
| `last_saved_by` | FK → `users` | |
| `created_at`, `updated_at` | timestamptz | |
| | | `UNIQUE (class_group_id, date)`: **one register per class per day** |
| | | `UNIQUE (id, class_group_id)`: target for the composite key from records |

A session exists only once saved, so "registers not taken today" is simply the active classes
with no session for today.

### `attendance_records`

| Column | Type | Rules |
|---|---|---|
| `session_id` | FK | |
| `class_group_id` | FK | composite `FOREIGN KEY (session_id, class_group_id) REFERENCES attendance_sessions (id, class_group_id)` |
| `enrollment_id` | FK | composite `FOREIGN KEY (enrollment_id, class_group_id, learner_id) REFERENCES enrollments (id, class_id, learner_id)` |
| `learner_id` | FK | |
| `status` | text | `present` \| `absent` \| `late` \| `excused` (decision A1 may add one) |
| `note` | text | optional, e.g. "arrived 08:40", "sent home sick" |
| `recorded_by` | FK → `users` | |
| `recorded_at`, `updated_at` | timestamptz | |
| | | `UNIQUE (session_id, learner_id)`: **no duplicate record for a learner in a register** (baseline §6) |

The two composite keys make three mistakes impossible at the database level: a record in one
class's register pointing at another class's session, a record using another learner's
enrollment, and a record for an enrollment in a different class. This needs one addition to slice
1: `UNIQUE (id, class_id, learner_id)` on `enrollments`. It is a new constraint over existing
columns; no table is reshaped.

The register lists learners whose enrollment in that class is active **on the register's date**
(start ≤ date, end null or ≥ date, not reversed). That spans tables, so it is checked by the
application. A learner transferred from 5A to 5B on 15 June appears in 5A's registers up to
14 June and 5B's from 15 June. Records keep pointing at the enrollment they were taken under, as
slice 1 planned.

Indexes: `(learner_id, session_id)` for a learner's history; `(class_group_id, date)` comes with
the unique constraint.

### 4.1 Who may take and change a register (decision A2)

Checked by one access function, `staffing.access.can_take_register(user, class_group, date)`:

| Who | Today and recent days (§5.3) | Older registers |
|---|---|---|
| Class teacher of that class on that date | take and edit | view |
| Subject teacher of that class, or a teacher with cover | view | view |
| Headteacher, Academic Administrator, System Administrator | view | correct, with a reason (including entering a register the class teacher missed) |

Only the class teacher takes the register (decision A2). When a class teacher is away, the school
makes a **temporary class-teacher assignment** for those dates (slice 1's
`class_teacher_assignments` already have start and end dates), or the class teacher fills in the
missed days on return within the back-fill window (§5.3). The `attendance` cover duty from slice 1
therefore grants nothing in this slice; it stays available should the school later want cover
teachers to take registers.

### 4.2 Corrections

Every save is audited (`attendance.save`, with the statuses that changed). An edit after the
editing window (§5.3) is a **correction**: it needs a reason and the `correct_attendance`
permission, and is audited as `attendance.correct` with old and new status per learner. Records are
updated in place; the audit log holds the history.

## 5. Absence early warning

### 5.1 The rule

A learner is flagged when they are marked **Absent on two consecutive school days**:

- Only `absent` counts. `late` and `excused` break the run, because the learner is accounted for.
- "Consecutive" means consecutive **register days (§3) on which the learner's class has a register**.
  A school day with no register neither extends nor breaks the run (decision A4), and shows up on
  the Headteacher's "registers not taken" list instead.
- Weekends, holidays and closures (school-wide, grade or class) never count, as the baseline requires.

The check runs in the same transaction as the register save, for the saved date. Back-filling or
correcting an older register re-checks that learner around that date. If a correction removes an
absence that triggered a case, the case is **not** closed automatically; a `system_note` action
records the change and the teacher decides.

### 5.2 `absence_cases`

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `learner_id` | FK | |
| `class_group_id` | FK | the class at the time of flagging |
| `assigned_teacher_id` | FK → `users` | class teacher when flagged; null if the class has none, in which case the case goes straight to the Headteacher's list |
| `status` | text | `new` \| `follow_up_pending` \| `in_progress` \| `resolved` \| `escalated` (baseline §6) |
| `first_absent_date`, `last_absent_date` | date | `CHECK (last_absent_date >= first_absent_date)` |
| `days_absent` | smallint | school days absent in the current run, kept up to date |
| `flagged_at` | timestamptz | |
| `escalated_at`, `escalated_by`, `escalation_reason` | | set when referred to the Headteacher |
| `resolved_at`, `resolved_by`, `resolution` | | `CHECK`: all three set if and only if `status = 'resolved'` |
| `created_at`, `updated_at` | timestamptz | |
| | | **one open case per learner:** partial `UNIQUE (learner_id) WHERE status <> 'resolved'` |

Further absences while a case is open extend that case (`last_absent_date`, `days_absent`) rather
than opening another. The case stays open, and visible, after the learner returns, until someone
resolves it. A new run of absences after resolution opens a new case.

**Status changes:**

| From | To | Who |
|---|---|---|
| new | follow_up_pending, in_progress, resolved, escalated | assigned teacher, Headteacher, Academic Administrator |
| follow_up_pending, in_progress | each other, resolved, escalated | assigned teacher, Headteacher, Academic Administrator |
| escalated | in_progress, resolved | Headteacher |
| resolved | — (final; a new run opens a new case) | |

### 5.3 Settings

Added to `school_settings`:

| Setting | Default | Meaning |
|---|---|---|
| `attendance_edit_days` | 2 | school days after the register date during which the teacher may still edit without a correction |
| `attendance_backfill_days` | 5 | how many school days back a teacher may take a missed register |
| `absence_stale_days` | 5 | an open case with no action for this many school days is highlighted as long-outstanding |

### 5.4 `absence_case_records`

Links a case to the absences behind it, so the case shows exactly which days triggered and
extended it.

| Column | Type | Rules |
|---|---|---|
| `case_id` | FK | |
| `attendance_record_id` | FK | `UNIQUE (case_id, attendance_record_id)` |

### 5.5 `follow_up_actions`

The case history. **Append-only**: a trigger refuses `UPDATE` and `DELETE`, as for `audit_logs`, so
earlier actions are never overwritten (baseline §6).

| Column | Type | Rules |
|---|---|---|
| `case_id` | FK | |
| `kind` | text | `contact` \| `note` \| `status_change` \| `referral` \| `system_note` |
| `contact_date` | date | required for `contact` |
| `contact_method` | text | `phone_call` \| `whatsapp` \| `sms_sent_manually` \| `home_visit` \| `met_at_school` \| `other`; required for `contact` |
| `contacted_guardian_id` | FK → `guardians` | optional |
| `outcome` | text | e.g. "Spoke to mother; learner has malaria" |
| `reason_category` | text | `illness` \| `family` \| `transport` \| `fees` \| `work` \| `unknown` \| `other`; optional |
| `reason_text` | text | |
| `next_action`, `next_action_due` | text, date | |
| `status_from`, `status_to` | text | set for `status_change` and `referral` |
| `created_by` | FK → `users` | null only for `system_note` |
| `created_at` | timestamptz | |

Changing a case's status writes the case row and a `status_change` action in one transaction.

## 6. Permissions

New permissions, added to the role mapping in `accounts/roles.py`:

| Action | Sys Admin | Headteacher | Academic Admin | Teacher |
|---|---|---|---|---|
| Manage calendar (holidays, extra school days) | yes | view | yes | view |
| Add or end closures (school, grade or class) | yes | yes | yes | — |
| Take or edit a register | — | — | — | class teacher only, per §4.1 |
| Correct a register, with a reason | yes | yes | yes | — |
| View attendance | all | all | all | classes they can see |
| Work on absence cases | all | all, plus escalations | all (decision A5) | cases in their class |

## 7. Screens

1. **Class register** (from the agreed design, without the M button). Class and date at the top;
   one row per learner with name, learner number and large P / A / L / E buttons that work on a phone; **Add note**
   under each row; search by name or number and filter by status; a fixed bottom bar with
   **Mark all present**, **Cancel** and **Save changes**. **Edit roster** is shown only to roles
   that manage enrollments and opens the class's learner list.
2. **My classes.** The class teacher's register for today: taken, not yet taken, a non-school day,
   or closed (with the closure's reason).
3. **Attendance overview** (Headteacher and administrators). Today's registers by class: taken,
   not taken, or closed; one click into any register.
4. **Absence follow-up**, the dedicated screen. Teachers see open cases in their classes. The
   Headteacher sees all cases, with escalated and long-outstanding ones first. Filters: status,
   class, date flagged, assigned teacher. A case shows recent attendance, the history, and forms to
   record a contact, change status or refer to the Headteacher.
5. **Learner profile → Attendance** tab (the placeholder slice 1 left): term summary and day list.
   **Learner profile → Absence follow-up** tab: that learner's cases, reading and writing the same
   records as screen 4.
6. **Calendar**: terms with their holidays, closures and extra school days, and a **Close a grade or
   class** form (grade or class, from date, optional end date, reason).

The home dashboard gains three "Waiting on you" items: registers not taken today, new absence
cases, and escalated cases (Headteacher).

## 8. Decisions

| # | Decision | Recommendation | Why |
|---|---|---|---|
| A1 | Statuses: the shared design showed an extra **M**. | **Agreed: no M.** Present, Absent, Late, Excused, as in the baseline. | |
| A2 | Who may take the register? | **Agreed: the class teacher only.** Office roles correct with a reason; a temporary class-teacher assignment covers absences. | One person owns each class's daily register. |
| A3 | Does a new register start blank or all Present? | **Agreed: blank**, with Mark all present; Save is refused while anyone is unmarked. | An accidental save cannot mark absent children present. |
| A4 | A school day with no register: does it break a run of absences? | **Agreed: neither breaks nor extends it**; it is listed on the Headteacher's "registers not taken". | A forgotten register should not hide a child who has missed days. |
| A5 | Can Academic Administrators work on absence cases? | **Agreed: yes**, on all cases. Reviewing escalations stays with the Headteacher. | |
| A6 | Who manages the calendar? | **Agreed: System and Academic Administrators; the Headteacher may also add closures**, including grade and class closures (§3.1). | |
| A7 | How far back may a teacher take or edit a register? | **Agreed: edit for 2 school days; fill in a missed register up to 5 school days back**; both adjustable in School setup. | |

## 9. Deferred

- Attendance percentages and absence lists in reports and exports (slice 6), and on report cards
  (slice 4).
- Notifying class teachers by email or SMS. Version 1 shows new cases on the dashboard and the
  follow-up screen only; the baseline forbids SMS to parents about absence.

## 10. Next steps

1. Build slice 2: migrations, calendar, holiday seed and grade/class closures, register screen, absence check, follow-up
   screen, profile tabs, dashboard items, tests for every database rule.
