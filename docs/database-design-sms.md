# Database Design — Slice 5: SMS to Guardians

**Status:** Draft for review — decisions S1–S7 need answers\
**Version:** 0.1\
**Date:** 4 November 2026\
**Implements:** Requirements baseline v1.1, §10 (results SMS, phone verification, communication preferences,
templates, scheduling, delivery and budget), §13 "Messaging and SMS controls", §15 (SMS actions audited)\
**Builds on:** guardians, contact history and verification (slice 1), learner–guardian links with primary and
alternative contacts and "receives results" (slice 1), classes and teacher access, the audit log.
**Results SMS** also needs approved report cards (slice 4); until then that part is built but switched off.

## 1. Scope

| Area | Tables |
|---|---|
| Phone verification | `phone_verifications` |
| Preferences | `guardian_communication_preferences` |
| Templates | `sms_templates` |
| Sending | `message_batches`, `message_logs`, `message_delivery_events`, `scheduled_messages` |
| Budget | `sms_budget_settings`, `sms_budget_exceptions` |

Out of scope: inbound SMS (replies, "STOP" keywords) — opt-outs are recorded by staff in Version 1 (S6);
WhatsApp and email; the guardian portal.

## 2. How it works

**Verifying a number (preferred: SMS code).** On a guardian's contact, staff press **Send code**. The guardian
receives a 6-digit code and reads it back to staff (by phone or in person), who enter it; the number is
marked verified with method `sms_code`. Staff may instead record another method (in person, phone call) with
the reason — already possible since slice 1. A changed number always starts unverified. Teachers can **flag
a number as wrong**; office staff then correct and re-verify it.

**Announcements.** Staff pick a template (parent meeting, term opening, exam schedule, closure, other), edit
the wording for this send, choose recipients (whole school, grades, classes) and see a **preview**: the exact
message per guardian with placeholders filled, how many guardians, SMS segments, estimated cost, warnings
(unverified numbers, opted-out guardians left out, budget). They then **send now** or **schedule** for later.
Scheduled batches can be edited or cancelled until they go.

**Results (after report cards).** When a report card is approved, one results SMS per learner can be sent:
to the primary contact if verified and allowed to receive results, otherwise the alternative contact under
the same test, otherwise **no SMS** and the learner is flagged for follow-up. Never to two guardians for one
learner; never before approval.

**At send time** every message is checked again: recipient still eligible, still opted in (announcements),
number still current and verified (results), and the batch still within the month's budget.

## 3. Tables

### `phone_verifications`

| Column | Type | Rules |
|---|---|---|
| `contact_id` | FK → `guardian_contacts` | |
| `code_hash` | text | the code is never stored in clear |
| `sent_at`, `expires_at` | timestamptz | valid 15 minutes (S3) |
| `attempts` | smallint | `CHECK (attempts <= 3)`; a fourth wrong attempt needs a new code |
| `verified_at`, `verified_by` | | set on success; the contact's verification fields are set in the same transaction |
| `message_log_id` | FK → `message_logs` | the SMS that carried the code |
| `requested_by` | FK → `users` | |
| | | one live code per contact: partial `UNIQUE (contact_id) WHERE verified_at IS NULL AND attempts < 3` (expired codes are replaced) |

Wrong-number flags reuse the contact record: `guardian_contacts.flagged_at`, `flagged_by`, `flag_reason`
(new columns), cleared when the number is changed.

### `guardian_communication_preferences`

One row per guardian: `announcements_opt_out` (boolean), `changed_at`, `changed_by`, `reason`. Results are not
governed by this flag (§10: separate); they follow "receives results" on each learner link and the
school's communication policy. Opting out never removes a guardian link.

### `sms_templates`

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `kind` | text | `parent_meeting` \| `term_opening` \| `exam_schedule` \| `closure` \| `other` \| `results` \| `verification` |
| `name`, `body` | text | body uses placeholders: `{guardian_name}`, `{learner_name}`, `{class}`, `{school}`, `{date}`, `{time}`, `{results}` … |
| `status` | text | `active` \| `retired` |
| `created_by`, `updated_by` | | |

Editing a template changes future sends only; a send stores its own copy of the wording (§10).

### `message_batches`

One send: an announcement, a results release or verification codes.

| Column | Type | Rules |
|---|---|---|
| `school_id` | FK | |
| `kind` | text | `announcement` \| `results` \| `verification` |
| `template_id` | FK, null | |
| `body` | text | the exact wording used for this batch |
| `audience` | jsonb | what was chosen: whole school / grades / classes / learners |
| `status` | text | `draft` \| `scheduled` \| `sending` \| `sent` \| `paused_budget` \| `cancelled` \| `failed` |
| `scheduled_for` | timestamptz, null | |
| `recipient_count`, `segment_count` | integer | |
| `estimated_cost`, `actual_cost` | numeric(10,2) | ZMW |
| `created_by`, `confirmed_by`, `confirmed_at`, `cancelled_by`, `cancelled_at` | | review and confirmation required before sending or scheduling (§10) |

`scheduled_messages` in the baseline is this batch's `scheduled_for` and `scheduled` status; no separate table
is needed (noted as a design simplification).

### `message_logs`

One row per SMS to one phone number; the exact text sent is stored (§10).

| Column | Type | Rules |
|---|---|---|
| `batch_id` | FK | |
| `guardian_id`, `contact_id` | FK | the number used at send time |
| `learner_id` | FK, null | for results and learner-specific announcements |
| `phone` | text | copied at send time, so history survives number changes |
| `body` | text | final text with placeholders filled |
| `segments` | smallint | GSM-7: 160 / 153 per part; Unicode: 70 / 67 |
| `status` | text | `queued` \| `sent` \| `delivered` \| `failed` \| `skipped` |
| `skip_reason` | text | e.g. `opted_out`, `unverified`, `no_eligible_contact`, `budget` |
| `provider_message_id` | text, null | |
| `estimated_cost`, `actual_cost` | numeric(8,2) | |
| `attempts`, `next_attempt_at` | | retry policy (S7) |
| | | **one results SMS per learner per report card:** partial `UNIQUE (learner_id, batch_id) WHERE status <> 'skipped'` on results batches |

### `message_delivery_events`

Append-only (trigger). Every status the provider reports: `log_id`, `event` (`accepted`, `delivered`,
`failed`, `rejected` …), `provider_code`, `occurred_at`, `raw` (jsonb).

### `sms_budget_settings` and `sms_budget_exceptions`

`sms_budget_settings`: one row per school — `monthly_budget` (ZMW), `cost_per_segment` (estimate), set by the
System Administrator, audited. `sms_budget_exceptions`: `batch_id`, `amount_over`, `reason`, `approved_by`,
`approved_at` — lets one batch go over budget (S4).

## 4. Sending, retries and the provider

- A **provider adapter** sends one message and reports status; the school's provider is a setting (S1).
  Until a provider account exists, the adapter runs in **test mode**: messages are logged as sent but
  nothing leaves the system, so everything can be tried safely.
- A **send job** (`manage.py send_due_messages`) sends confirmed batches whose time has come, retries
  temporary failures, and pauses batches that would exceed the budget. It must run every few minutes (S2).
- **Delivery reports** arrive at a webhook URL secured with a provider secret and are stored as delivery
  events.
- Every confirmation, cancellation, budget exception and template change is audited.

## 5. Permissions

| Action | Sys Admin | Headteacher | Academic Admin | Teacher |
|---|---|---|---|---|
| Send verification codes, record verification | yes | yes | yes | — |
| Flag a number as wrong | yes | yes | yes | learners they can see |
| Manage shared templates | yes | yes | if granted individually | — |
| Send announcements | whole school | whole school | whole school | guardians of their classes (S5) |
| Release results SMS (after report cards) | yes | yes | — | — |
| Set the monthly budget | yes | — | — | — |
| Approve a budget exception | yes | yes | — | — |
| Record an opt-out | yes | yes | yes | — |

## 6. Screens

1. **Messages**: batches with status, recipients, cost; scheduled ones can be edited or cancelled.
2. **New announcement**: template → wording → recipients → **preview** (exact message, recipients, segments,
   cost, warnings) → send now or schedule.
3. **Templates** (shared): list, edit, retire.
4. **Guardian contact**: Send code / enter code, record another method, flag wrong number, opt out of
   announcements, message history for that number.
5. **SMS budget**: this month's spend vs budget, batches paused for budget, exceptions.
6. **Results release** (with slice 4): approved report cards ready to send, learners flagged with no eligible
   contact.

## 7. Decisions

| # | Question | Recommendation |
|---|---|---|
| S1 | **Which SMS provider?** | **Africa's Talking**: serves Zambia (MTN, Airtel, Zamtel), local pricing in kwacha, delivery reports, and a branded sender ID once approved. Twilio works but costs more per SMS; a local aggregator is fine if the school already has one. Whichever, the system starts in **test mode** until the account and sender name are ready. **Do you have an existing provider or account?** |
| S2 | **Running scheduled sends and retries** (Render's free plan has no background jobs) | **A Render Cron Job** running the send job every 5 minutes (a small monthly charge on Render). The free alternative is an external scheduler (e.g. cron-job.org) calling a secured URL; it works but is less dependable. |
| S3 | Verification code | **6 digits, valid 15 minutes, 3 attempts**; the guardian reads it back to staff (no reply SMS needed). |
| S4 | Budget | **Monthly budget in ZMW set by the System Administrator**; a batch that would exceed it is **paused** (never half-sent) and the System Administrator and Headteacher are told; either may approve an exception with a reason. |
| S5 | Who can send announcements | **System Administrator and Headteacher to anyone; Academic Administrator to anyone; teachers only to guardians of their own classes.** No extra approval; the budget applies to everyone. |
| S6 | Opt-outs | **Recorded by staff** when a guardian asks (Version 1 has no inbound SMS); results are unaffected. |
| S7 | Retries when the provider is down | **3 retries after 5, 15 and 60 minutes**, then marked failed and listed on the Messages screen. |

## 8. Next steps

1. Answer S1–S7 (S1 and S2 involve accounts or a small cost; nothing is bought or created without your go-ahead).
2. Build slice 5 in test mode: verification codes, preferences, templates, announcements with preview and
   scheduling, budget, send job, delivery webhook; results SMS switched on with slice 4.
