# Database Design — Promotions and Transfers with Approval

**Status:** Approved 3 November 2026 — P1–P7 accepted as recommended\
**Version:** 1.0\
**Date:** 3 November 2026\
**Implements:** Requirements baseline v1.1, §5 "Requests and approvals", "Transfers", "Mid-term transfer report
card rule"; completes the `enrollments.request_id` link that [slice 1](database-design-foundation.md) left
for this work\
**Builds on:** slice 1 (enrollments with the no-overlap exclusion constraint, transfer and correction
services, audit log), slice 2 (attendance records stay with their enrollment), slice 3 (term results,
shown to help decide promotions)\
**Why now:** Term 3 ends in early December. Every learner then needs a 2027 class; without this, staff
would create each enrollment by hand.

## 1. Scope

| Area | Tables |
|---|---|
| Requests | `enrollment_requests` (one row per learner per transfer or year-end decision) |
| Year-end batches | `promotion_batches` (one per class per year end) |

Changed: `enrollments.request_id` becomes a foreign key to `enrollment_requests`. The existing
immediate transfer (office roles only) is replaced by the request flow (decision P6).

Out of scope: report cards (slice 4) — the mid-term transfer rule (report card under the new class only)
is applied there; exam-class rules (Phase 2).

## 2. How it works

**Year-end promotion (bulk)**

1. When next year exists with its classes (status `planned`), the **class teacher** opens their class's
   **promotion sheet** (decision P1). Every learner enrolled at year end is listed with their Term 3
   overall result and attendance rate, and a pre-filled decision: **promote** to the same stream of the
   next grade (5A → 6A, Grade 7 → Form 1; decision P2).
2. The teacher changes decisions where needed — **repeat** (same grade next year), **hold for review**,
   **complete** (final grade, decision P3) or **leaving** (to another school) — and picks destination
   classes. Repeat, hold and leaving need a note.
3. **Submit** is refused while any learner has no decision, a promote/repeat has no destination, or a
   destination is in the wrong year or grade.
4. The **Headteacher** reviews the sheet (decision P4) and either **approves it** — after a confirmation
   page showing exactly what will happen ("28 promoted to 6A, 2 repeat 5A, 1 held") — or **returns it**
   with a note for the teacher to fix (decision P5).
5. On approval, in one transaction per class: each current enrollment ends on the last day of the year
   (`end_reason` promotion, repeat or completion), and a new enrollment starts on the first day of next
   year in the destination class, linked by `previous_enrollment_id` and `request_id`. Held learners get
   no new enrollment and appear on the Headteacher's "held for review" list until decided individually.

**Within-year transfer (individual)**

1. A teacher of the learner, or an office role, requests a move to another class in the same year with
   an effective date and a reason (decision P6).
2. The Headteacher approves or rejects it. On approval the slice 1 transfer runs: the old enrollment
   ends the day before the effective date, the new one starts on it, and earlier attendance and marks
   stay with the old class.

## 3. Tables

### `promotion_batches`

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `from_class_id` | FK → `classes` | the class being promoted |
| `from_year_id`, `to_year_id` | FK → `academic_years` | `CHECK (to_year_id <> from_year_id)`; to-year must start after from-year ends (application) |
| `status` | text | `draft` \| `submitted` \| `returned` \| `approved` |
| `prepared_by` | FK → `users` | |
| `submitted_at`, `decided_by`, `decided_at`, `decision_note` | | set on submit / approve / return |
| | | **one batch per class:** `UNIQUE (from_class_id)` — a class is promoted once, so a batch can never be processed twice |

### `enrollment_requests`

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `kind` | text | `promotion` \| `transfer` |
| `batch_id` | FK → `promotion_batches`, null | set for promotions, null for transfers |
| `learner_id` | FK | |
| `from_enrollment_id` | FK → `enrollments` | the enrollment being ended |
| `decision` | text, null | promotions: `promote` \| `repeat` \| `hold` \| `complete` \| `leaving`; null for transfers |
| `to_class_id` | FK → `classes`, null | required for promote, repeat and transfer; null otherwise |
| `effective_date` | date | transfers: the first day in the new class; promotions: next year's start date |
| `note` | text | required for repeat, hold, leaving and transfers |
| `status` | text | `draft` \| `pending` \| `approved` \| `rejected` \| `cancelled` |
| `requested_by`, `decided_by`, `decided_at`, `decision_note` | | |
| `resulting_enrollment_id` | FK → `enrollments`, null | the enrollment created on approval |
| `created_at`, `updated_at` | timestamptz | |
| | | `CHECK`: `to_class_id` set if and only if decision is promote/repeat or kind is transfer |
| | | **one decision per learner per batch:** `UNIQUE (batch_id, learner_id) WHERE batch_id IS NOT NULL` |
| | | **one open transfer request per learner:** `UNIQUE (learner_id) WHERE kind = 'transfer' AND status = 'pending'` |
| | | an approved request has a decider: `CHECK (status NOT IN ('approved','rejected') OR decided_by IS NOT NULL)` |

`enrollments.request_id` gets a foreign key to `enrollment_requests (id)`.

### Validation (application, checked again inside the approval transaction)

- The destination class belongs to the batch's to-year (promotions) or the learner's current year
  (transfers), and is active.
- Promote goes to the next grade by promotion order; repeat stays in the same grade. A different grade
  is refused (decision P7).
- No learner is enrolled in the to-year already (a second promotion would be refused by the slice 1
  exclusion constraint anyway; the check gives a clear message first).
- Every learner enrolled in the class on the last day of the year has a decision before submit.
- The approval runs inside one transaction with the batch row locked, so two Headteachers clicking
  Approve at once cannot process it twice.

### Corrections and reversals

A promotion or transfer that turns out wrong is fixed with slice 1's enrollment correction (the new
enrollment is marked `reversed` and the right one created), with a reason and an audit record. The
request keeps its history. Report cards approved before the correction keep their own copy of the class
(slice 4).

## 4. Permissions

| Action | Sys Admin | Headteacher | Academic Admin | Teacher |
|---|---|---|---|---|
| Prepare a class's promotion sheet | yes | yes | yes | class teacher of that class (P1) |
| Request a transfer | yes | yes | yes | teachers of the learner (P6) |
| Approve or return promotions, approve or reject transfers | — | yes (P4) | — | — |
| See requests | all | all | all | their own and their class's |

## 5. Screens

1. **Promotions** (year end): one card per class with its sheet status (not started, draft, submitted,
   returned, approved).
2. **Promotion sheet**: learners as rows with Term 3 overall result, attendance rate, decision dropdown,
   destination class and note; **Save** and **Submit to the Headteacher**; the returned note shown at
   the top.
3. **Approvals** (Headteacher): submitted sheets and pending transfers; a sheet opens a summary
   ("28 → 6A, 2 repeat, 1 held, 1 leaving") and a confirmation step before **Approve**, or **Return
   with a note**.
4. **Held for review**: learners with no next-year class yet, each decided individually.
5. **Learner profile → Enrolment** tab: **Request a transfer** and the learner's request history.
6. Home dashboard: "promotion sheets awaiting approval" and "transfer requests" for the Headteacher;
   "promotion sheet not submitted" for class teachers once next year exists.

## 6. Decisions

| # | Question | Recommendation |
|---|---|---|
| P1 | Who prepares a class's promotion sheet? | **The class teacher**; office roles can too (e.g. if the class teacher has left). |
| P2 | Pre-filled destination | **Same stream, next grade** (5A → 6A; Grade 7A → Form 1A). If that class doesn't exist next year, the teacher picks one. |
| P3 | Final-grade learners (Form 4) | Decision **complete**: their enrollment ends and their status becomes *completed*. Grade 7 learners continuing to Form 1 here are ordinary promotions; those going to another secondary school are **leaving** (status *transferred*). |
| P4 | Who approves? | **The Headteacher only**, as the baseline says. |
| P5 | Can the Headteacher change individual decisions while approving? | **No: approve the whole sheet or return it with a note**; the teacher fixes and resubmits. Keeps one person responsible for each decision. |
| P6 | Within-year transfers | **All transfers go through a request** (replacing today's immediate office transfer). A teacher of the learner or an office role requests; the Headteacher approves. A Headteacher's own request takes effect at once, recorded as approved by them. |
| P7 | Promote or repeat into an unexpected grade (e.g. skipping a grade) | **Refused.** A rare exception is handled afterwards with an enrollment correction and a reason. |

## 7. Next steps

1. Answer P1–P7.
2. Build: migrations, promotion sheets, approvals with confirmation, transfer requests, held list,
   dashboard items, tests for every rule (no double processing, no conflicting enrollments, missing
   decisions).
