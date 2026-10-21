# School Management System — Requirements and Design Agreement

**Document status:** Requirements baseline / working specification\
**Version:** 1.1\
**Date:** 21 October 2026\
**Purpose:** Consolidate the decisions made so far before choosing the
technology stack and starting implementation.

> **Important:** This document records agreed requirements and
> recommendations that have been accepted. Items explicitly skipped or
> not yet answered are marked **Pending**. Technology selection and
> coding have not started.

### Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 21 October 2026 | Initial baseline. |
| 1.1 | 21 October 2026 | Confirmed a dedicated absenteeism follow-up screen (§6, §12). Confirmed that teachers can preview/download draft report-card PDFs before submission (§9, §12). Removed both from the pending lists (§16, §18). |

## 1. Project overview

Build a school management system for a Zambian school. Version 1 is for
one school, but the architecture and data model should be designed so it
can later support multiple schools with central oversight.

### Main priorities

1.  Learner attendance.
2.  Learner academic performance and competency development.
3.  Report cards and approval.
4.  Parent/guardian communication by SMS.
5.  Learner history, reporting, and absenteeism follow-up.
6.  Secure staff access and academic administration.

School fees are **not included in Version 1** and may be added later. A
parent/guardian portal is also deferred to a later phase.

### Design principles

-   Friendly, colorful **blue-and-white** interface.
-   Responsive layouts for desktop, tablet, and mobile.
-   Permanent learner identity across the learner's entire school
    career.
-   Preserve historical records rather than overwriting them.
-   Enforce permissions on the server, not only by hiding interface
    controls.
-   Keep an audit trail for important actions and corrections.
-   Configure curriculum, subjects, and grading rules without changing
    historical results.
-   Keep the first release manageable while allowing future expansion.

## 2. School structure and curriculum

-   Support Grades 1–7 and Forms 1–4.
-   Support parallel classes, such as Grade 1A, Grade 1B, Grade 1C, and
    multiple classes within each form.
-   Subjects can differ by grade/form and academic period.
-   Support Zambia's competence-/skills-based curriculum.
-   Support both numerical marks and competency levels.
-   Curriculum frameworks, subjects, competency scales, grading schemes,
    and related settings must be configurable and versioned.
-   Changes to curriculum or grading settings must have an effective
    academic year/period or version. Historical results and approved
    report cards must retain the rules that applied when they were
    recorded.
-   Grades 1–7 and Forms 1–4 are treated as separate educational
    stages in comparisons.
-   Specialized examination-class calculations are deferred to Phase 2;
    the school will provide those rules later.

## 3. User roles and access

### Roles

-   **System Administrator**
-   **Headteacher**
-   **Teacher**
-   **Academic Administrator**

A parent/guardian portal is deferred to a later phase.

### General account rules

-   Staff can sign in using a staff ID or username/email and password.
-   System Administrator and Headteacher can create staff accounts and
    assign roles within their permitted limits.
-   Account deactivation is preferred to routine deletion.
-   Deactivation must revoke access immediately, including active
    sessions.
-   Important account, permission, and record changes must be audited.
-   Sensitive learner data is accessible only where there is an
    operational need.
-   Server-side permission checks are mandatory.

### Teacher access

-   Teachers can access only the classes and subjects assigned to them
    for the relevant academic period.
-   A teacher's permission is tied to a **class-and-subject
    assignment**. Assignment to a subject in one class does not
    automatically grant access to that subject in another class.
-   Teachers can take attendance and manage permitted academic records
    within their assignments.
-   Teachers can initiate promotion or transfer requests.
-   Teachers can submit results for their assigned classes after the
    required Headteacher approval.
-   Teachers can flag incorrect guardian contact details for authorized
    staff to review.
-   Teachers are responsible for following up on learners flagged for
    repeated absence.

### Headteacher access

-   School-wide oversight.
-   View all classes and the full learner history.
-   Approve and sign report cards.
-   Approve or reject class transfer and promotion requests.
-   Send results for any class after the required approval process.
-   Review outstanding or escalated absence cases.
-   Approve exceptions for incomplete report cards, with a recorded
    reason.

### Academic Administrator

-   Manage academic records within assigned permissions.
-   Use approved communication templates and other permitted academic
    tools.
-   Shared-template management is allowed only if explicitly authorized.

### System Administrator

-   Full system and configuration access.
-   Manage accounts, roles, permissions, system settings, and other
    authorized configuration.
-   Sensitive and high-impact actions remain subject to audit.

### Teacher cover

-   Teacher assignments can have primary and backup roles.
-   Backup access during a primary teacher's absence requires a
    time-bound cover authorization that specifies the permitted duties.

## 4. Learner identity, registration, and profiles

### Permanent learner identity

-   Each learner has one permanent learner ID for their entire school
    career.
-   Moving to another class, grade, or academic year must not create a
    new learner ID.
-   Attendance, results, competencies, report cards, and follow-up
    records must remain linked to the learner across years and classes.
-   Routine historical records must not be deleted or overwritten.

### Individual registration

Authorized staff can register a learner directly. Headteacher approval
is not required for normal registration.

The registration form includes:

**Learner details**

-   First name — required.
-   Last name — required.
-   Date of birth.
-   Gender.

**Enrollment/class**

-   Class — required.
-   Enrollment date — required.
-   Previous school — optional.

**Guardian**

-   Full name — required.
-   Relationship.
-   Mobile number — required and subject to verification for SMS.

**Additional information**

-   Important medical or support notes — optional, restricted to
    authorized users and minimized to what is operationally necessary.

Before creation, the system checks for likely duplicates. The system
generates the permanent learner ID. Newly registered learners appear in
class registers once the enrollment is created.

### Bulk registration

Support both individual registration and bulk registration through an
official Excel template.

Recommended import workflow:

1.  Download the official template.
2.  Enter learner and guardian information.
3.  Upload the completed file.
4.  Validate required fields, formats, and likely duplicates.
5.  Show a preview and identify invalid rows.
6.  Allow corrections and re-upload or correction before confirmation.
7.  Confirm the import.
8.  Create valid learner records and generate permanent IDs.
9.  Produce an import summary/error report and audit the import.

Valid registrations take effect without Headteacher approval. Duplicate
prevention applies to both individual and bulk registration.

### Learner status and profile

Learner statuses include active, transferred, withdrawn, and completed.
Archive or inactivate records rather than routinely deleting them.

One learner profile screen will use tabs for:

1.  Personal details.
2.  Guardians and contacts.
3.  Enrollment and class history.
4.  Attendance.
5.  Academic results.
6.  Competency development.
7.  Report cards.
8.  Absence follow-up.

The profile brings together the learner's history through the permanent
learner ID. Access to sensitive information depends on staff
permissions.

### Guardian relationships

-   One guardian can be linked to multiple learners.
-   One learner can have multiple guardians.
-   This is a many-to-many relationship.
-   Guardian access must be explicitly authorized for each learner,
    including contact arrangements.
-   Removing guardian access must not delete historical contact or
    message records.
-   Results SMS goes only to an appropriate, verified, authorized
    contact.

## 5. Enrollment, promotion, and class transfers

### Enrollment history

A learner may have multiple dated enrollment records over time. Each
enrollment links the learner to a class and academic year/period.

Enrollment records include:

-   Enrollment ID.
-   Learner ID.
-   Academic year.
-   Class.
-   Start date and end date.
-   Enrollment status.
-   Optional previous enrollment link.
-   Creator.
-   Approver and approval date where applicable.
-   Reason and timestamps.

There must not be conflicting active class enrollments for the same
learner and period. Enforce this transactionally in the
database/application.

### Transfers

-   A transfer retains the old enrollment with an end date and creates a
    new enrollment with an effective date.
-   Attendance and assessment records stay attached to the
    enrollment/class context in which they were recorded.
-   Example: if a learner transfers from Grade 5A to Grade 5B on 15
    June, records before the transfer remain under Grade 5A and records
    after the approved transfer belong to Grade 5B.
-   The learner's full history remains available.

### Mid-term transfer report card rule

-   Issue the Term 2 report card under the **new class only**.
-   Retain previous-class records in the learner's history.
-   Do not display a transfer-date note on the report card.
-   If required results in the new class are incomplete, apply the
    existing incomplete-report rules.

### Requests and approvals

-   A teacher may initiate a learner promotion or class-transfer
    request.
-   The Headteacher approves or rejects it before the official
    enrollment change takes effect.
-   Annual promotion and within-year transfer follow the same approval
    principle.
-   Support individual and bulk promotion.
-   Promotion decisions include promote, repeat, or hold for review.
-   Validate destination classes, missing decisions, duplicate
    processing, and conflicting enrollments.
-   Require confirmation before processing a bulk operation.
-   Corrections and reversals must preserve audit history. Previously
    approved report cards remain preserved if the class assignment is
    later corrected.

## 6. Attendance

### Daily attendance workflow

Attendance is recorded **daily**, not by individual lesson period.

1.  Teacher chooses class and date.
2.  Teacher selects **Mark All Present**.
3.  Teacher changes individual learners to Absent, Late, or Excused as
    appropriate.
4.  Teacher can search by learner name or permanent learner ID and
    filter by status.
5.  Teacher checks the register and corrects mistakes before submitting.
6.  Teacher saves/submits the register.

Attendance statuses:

-   Present
-   Absent
-   Late
-   Excused

The system prevents duplicate attendance records for a learner in the
same daily register. Authorized corrections are audited. Attendance
screens must work on mobile and desktop.

### School calendar

The system maintains a school calendar that identifies school days,
weekends, holidays, and school closures. Weekends, holidays, and
closures must not count as school days for absence alerts.

### Absence early warning and follow-up

-   Flag a learner after **two consecutive school days absent**.
-   Notify the assigned class teacher.
-   Keep the flag visible for review even after the learner returns.
-   Do **not** automatically SMS parents about absence.
-   The class teacher checks the attendance and contacts the
    parent/guardian when appropriate.
-   The teacher records contact date, method, outcome, reason if known,
    and next action.
-   If unresolved or requiring management attention, refer the case to
    the Headteacher.
-   The Headteacher reviews outstanding and escalated cases.
-   Teachers update cases as follow-up continues.
-   Preserve the full history of follow-up actions.
-   No social-services staff module is required for this school.

Case statuses:

-   New
-   Follow-up pending
-   In progress
-   Resolved
-   Escalated

### Absenteeism follow-up screen

**Confirmed (v1.1):** Version 1 includes a dedicated absenteeism
follow-up screen, separate from the Absence follow-up tab on the learner
profile. Both read and write the same case records.

-   **Teachers** see the open cases for learners in their assigned
    classes.
-   **Headteacher** sees all cases school-wide, with escalated and
    long-outstanding cases highlighted for review.
-   Cases can be filtered by status, class, date flagged, and assigned
    teacher.
-   From a case, staff can view the learner's recent attendance, record
    a follow-up action (contact date, method, outcome, reason if known,
    next action), change the case status, and refer the case to the
    Headteacher.
-   Every action is added to the case history. Earlier actions are never
    overwritten.
-   The dashboard's "tasks needing attention" section links to this
    screen for open and escalated cases.

## 7. Assessments and academic results

### Assessment entry

Teachers choose a class and subject, then enter:

-   Numerical marks.
-   Assessment details/type.
-   Competency information where applicable.
-   Individual learner comments.

Support both:

-   Table-based entry for a class.
-   Individual learner record view for detailed comments and
    corrections.

Both views use the same underlying draft data. Do not include a
class-level comment; comments are individual.

### Drafts and submission

-   Auto-save drafts and show a clear status: Saving, Saved, or Save
    failed.
-   Provide a manual **Save Draft** option.
-   Saving a draft does not submit it.
-   Teachers can edit marks while the record is a draft.
-   Submit the whole class in one action only after each learner has a
    mark or an explicit missing-assessment status.
-   Validate and show errors before submission.
-   Corrections after submission require the permitted correction
    workflow, a reason, and an audit record.
-   Teachers can make and approve their own corrections within their
    assigned classes/subjects, with the reason and audit history
    retained.

### Missing assessments

Missing results must never automatically become zero.

Supported statuses:

-   Absent
-   Excused
-   Not Yet Assessed

The applicable grading scheme determines whether a missing result
prevents a final score or position. Missing values must not be treated
as zero unless an explicitly approved scheme defines a valid rule for
the situation.

### Competency assessment

-   Competencies are recorded **at the end of each term only**, not
    continuously throughout the term.
-   Competency frameworks and level labels are configurable and
    versioned according to the approved curriculum.
-   The class view uses learners as rows and competencies as columns,
    with dropdowns for levels.
-   An individual learner view supports comments and corrections.
-   Missing competency information uses **Not Assessed**, not the lowest
    level.
-   Incomplete competency results are flagged before report-card
    approval.
-   Submitted corrections are audited.

## 8. Grading, calculations, and rankings

-   Calculate totals, averages, and subject grades using approved
    grading schemes.
-   Support configurable rounding, grading boundaries, and assessment
    weights.
-   Preserve the exact grading-scheme version used for each
    result/report.
-   A learner with unresolved required assessments does not receive a
    final overall score or position unless the approved scheme permits
    calculation with missing results.
-   A Headteacher may approve a report card with an unresolved missing
    assessment only by explicitly authorizing an exception and recording
    a reason.
-   In that case, show the missing status and keep the overall
    score/position pending; do not convert the missing result to zero.
-   Later corrections/revisions require the relevant approval and
    reapproval workflow.

### Optional rankings

-   Rankings are optional and disabled until an authorized administrator
    enables them.
-   Possible rankings include class position, grade/form position across
    parallel classes where results are comparable, and subject ranking
    where appropriate.
-   Competency and progress information remains visible regardless of
    whether rankings are enabled.
-   Class position is based on final overall term score; subject
    rankings are separate.
-   Use competition ranking for ties: 1st, 2nd, 2nd, 4th.
-   Compare/rank only comparable results and clearly flag differences in
    curriculum, assessment scale, or reporting period.
-   Changing ranking settings must not alter already approved report
    cards.

## 9. Report cards

### Contents and appearance

Use a blue-and-white report-card design. Include:

-   School name.
-   Academic year and term.
-   Learner name, permanent learner ID, and class.
-   Attendance summary.
-   Subject marks and grades.
-   Competency development.
-   Teacher comments.
-   Progress comparison across terms.
-   Headteacher approval/signature.

### Workflow

1.  Teacher enters and submits results.
2.  Headteacher reviews the report card.
3.  Headteacher approves and signs it.
4.  Only approved report cards may be sent as official results by SMS.

The Headteacher can approve report cards individually or in bulk. Each
learner's approval is logged individually. Incomplete report cards must
not be accidentally bulk-approved.

### Draft report-card PDFs

**Confirmed (v1.1):** Teachers can preview and download a draft
report-card PDF before submitting results to the Headteacher.

-   Teachers can generate draft PDFs only for learners in their assigned
    classes, using the current draft or submitted data.
-   The Headteacher can preview/download the draft PDF before approval.
-   Any pre-approval PDF must be clearly marked **DRAFT — NOT APPROVED**
    on every page and must not be sent as official results.
-   The system must not attach a draft PDF to an SMS or any other
    official results message.
-   Generating a draft PDF does not submit results or change their
    status.

### Historical versions and corrections

-   Approved report content is immutable.
-   Corrections require authorization, a recorded reason, and
    reapproval.
-   Preserve prior approved versions.
-   Later changes to curriculum, grading, ranking, or school settings
    must not silently change historical approved reports.

## 10. Parent/guardian communication by SMS

### Results SMS

-   Send results only to a verified, authorized guardian contact.
-   Send one results SMS per learner to minimize cost.
-   Use the designated primary guardian if eligible; otherwise use one
    pre-designated alternative guardian.
-   Never send duplicate results to multiple guardians for the same
    learner.
-   If no eligible verified contact exists, flag the learner for
    follow-up.
-   The system must not send official results before report-card
    approval.

### Phone verification

-   Preferred verification method: SMS one-time code.
-   Authorized staff may record an alternative verification method with
    staff member, date, method, and audit trail.
-   A changed number requires new verification.
-   Teachers can flag incorrect numbers; authorized staff update and
    verify them.
-   Retain historical contact details and message history.

### Communication preferences

-   Guardians can opt out of general announcements.
-   General-announcement preferences and learner-results communication
    are separate.
-   Opting out of general announcements does not delete the guardian
    relationship.
-   Apply the school's communication policy to results messaging.

### General SMS templates

Provide templates for:

-   Parent meetings.
-   Term opening.
-   Examination schedules.
-   School closures.
-   Other approved announcements.

Authorized staff can customize wording before sending, using
placeholders. Show a preview and confirmation. Store the exact content
sent.

-   System Administrator and Headteacher manage shared templates.
-   Teachers can use templates and customize wording for permitted
    recipients without changing the original shared template.
-   Academic Administrators can manage shared templates only if
    authorized.

### Scheduling, delivery, and budget

-   Allow announcements to be scheduled for a future date/time.
-   Before scheduling, require review and confirmation.
-   Messages can be edited or cancelled before sending.
-   At send time, recheck recipient eligibility, preferences, and
    budget.
-   Define a retry policy for temporary SMS provider unavailability.
-   System Administrator sets a monthly SMS budget.
-   Check immediate and scheduled batches against the budget.
-   Block or automatically pause a batch that would exceed the budget
    and notify staff.
-   An exception may be approved with a reason and audit trail.
-   Store estimated cost and actual provider charge where available.
-   An SMS preview screen shows exact message, recipients, number of SMS
    segments, estimated cost, warnings, and confirmation before sending.

## 11. Reports and dashboards

### Dashboard

A responsive, role-specific dashboard has three main sections:

1.  Key statistics.
2.  Tasks needing attention.
3.  Quick actions.

Show only the information and actions appropriate to the signed-in
user's permissions.

### Report types

-   Attendance.
-   Absenteeism and follow-up.
-   Academic performance.
-   Competency development.
-   Report-card status.
-   Individual learner history.
-   Enrollment and class lists.

### Drill-down and filters

Reports support:

-   Whole-school level.
-   Grade/form comparisons.
-   Specific class.
-   Individual learner.

Filters include academic year, term, class, subject, report type, and
date, as appropriate.

### Individual learner report

Include:

-   Profile and enrollment history.
-   Attendance statuses, percentages, absence dates, and follow-up.
-   Academic performance by subject, assessment type, term, and year.
-   Competency development.
-   Approved report cards.
-   Trends and interventions/follow-up history.

### Comparisons

-   Compare attendance, performance, competency levels, progress over
    time, and classes.
-   Grade-level comparisons are required.
-   Compare only equivalent subjects, assessment scales, and reporting
    periods.
-   Clearly flag differences in curriculum or scale.
-   Treat Grades 1–7 and Forms 1–4 as separate educational stages.

### Exports and access

-   Support PDF, Excel, and print-ready exports.
-   Export permissions must match on-screen permissions.
-   Sensitive exports must be audited.
-   Statistics should be calculated from source records rather than
    duplicated in separate aggregate fields unless there is a justified
    performance need.

## 12. Screen design inventory

The system should include the following screens or screen groups:

1.  **Dashboard** — role-specific statistics, attention items, quick
    actions.
2.  **Learner registration** — individual registration and duplicate
    checks.
3.  **Bulk learner import** — Excel template, validation preview,
    error report, confirmation.
4.  **Learner profile** — tabs for personal details, guardians,
    enrollment history, attendance, results, competencies, report cards,
    and follow-up.
5.  **Class/grade/form and subject management** — configurable
    academic structure, subjects, curriculum versions, and grading
    scales. Detailed approval rules were skipped and remain pending.
6.  **Daily attendance** — class/date selection, Mark All Present,
    individual status changes, search/filter, save, and correction.
7.  **Absenteeism follow-up** — cases, contact actions, status,
    referral, and Headteacher review. Dedicated screen confirmed (see
    §6).
8.  **Assessment entry** — table and individual views, draft
    auto-save, missing-result statuses, validation, submission, and
    audited corrections.
9.  **Competency assessment** — end-of-term competency matrix,
    dropdown levels, individual comments, Not Assessed status.
10. **Report cards** — teacher and Headteacher draft preview/PDF
    (watermarked DRAFT — NOT APPROVED), draft status, approval,
    signature, versioning, and reapproval.
11. **SMS/message centre** — templates, recipients, verification
    eligibility, preview, cost, budget checks, sending, scheduling,
    delivery status, and history.
12. **Reports and analytics** — drill-down, filters, grade/class
    comparisons, exports.
13. **User management** — staff accounts, roles, class/subject
    assignments, deactivation, cover authorization, audit logs.
14. **Promotion and transfer management** — requests, approvals,
    individual/bulk processing, and enrollment history.
15. **System settings and audit** — school configuration, academic
    periods, curriculum/grading versions, ranking controls,
    communication settings, and audit review.
16. **Future fees module** — not in Version 1.
17. **Future parent/guardian portal** — deferred to a later phase.

## 13. Conceptual database design

This is a conceptual entity/table inventory, not a final physical
schema. Exact field types, indexes, constraints, foreign keys, and
normalization will be finalized during database design.

### School and academic structure

-   `schools`
-   `school_settings`
-   `academic_years`
-   `terms`
-   `grades`
-   `classes`
-   `subjects`
-   `class_subjects`
-   `curriculum_versions`
-   `school_calendar`

### Learners, guardians, and enrollment

-   `learners`
-   `guardians`
-   `learner_guardians`
-   `enrollments`
-   `promotion_transfer_requests`
-   Promotion/transfer decision records
-   `learner_support_notes` (restricted)

### Users, roles, and security

-   `users`
-   `roles`
-   `user_roles`
-   `permissions`
-   `role_permissions`
-   `user_permissions`
-   `teacher_assignments`
-   `cover_authorizations`
-   `user_sessions`
-   `audit_logs`

### Attendance and follow-up

-   `attendance_sessions`
-   `attendance_records`
-   `absence_cases`
-   `follow_up_actions`

### Assessment and competency

-   `assessment_types`
-   `grading_schemes`
-   `assessment_components`
-   `assessments`
-   `assessment_results`
-   `assessment_result_history`
-   `competency_frameworks`
-   `competencies`
-   `competency_levels`
-   `learner_competency_results`

### Report cards

-   `report_cards`
-   `report_card_subjects`
-   `report_card_approvals`
-   `report_card_signatures`
-   `report_card_versions`
-   `ranking_settings`

### Messaging and SMS controls

-   `sms_templates`
-   `message_batches`
-   `message_logs`
-   `message_delivery_events`
-   `scheduled_messages`
-   `sms_budget_settings`
-   `sms_budget_exceptions`
-   `guardian_communication_preferences`
-   `phone_verifications`

### Reporting and exports

-   `report_exports`

### Core data rules

-   Use a permanent learner ID.
-   Preserve dated enrollment history.
-   Tie attendance and assessment records to the correct learner
    enrollment/class context.
-   Prevent conflicting active enrollments.
-   Enforce foreign keys and uniqueness rules where appropriate.
-   Use transactions for registration, promotion, transfer, and bulk
    operations.
-   Version curriculum, competency frameworks, and grading schemes.
-   Preserve approved report-card versions.
-   Record important changes, approvals, corrections, exports, and
    communication actions in audit/history records.
-   Scope major records by school, or through a clear school-scoping
    relationship, to support future multi-tenancy.
-   Restrict sensitive notes and contact details by role and operational
    need.

## 14. Suggested conceptual fields for key tables

These are starting points, not a finalized SQL schema.

### `schools`

-   `school_id` — primary key
-   `school_name`
-   `school_code`
-   `address`
-   `phone`
-   `email`
-   `status`
-   `created_at`

### `learners`

-   `learner_id` — internal primary key
-   `school_id`
-   `learner_number` — permanent, unique learner identifier
-   `first_name`
-   `last_name`
-   `date_of_birth`
-   `gender`
-   `registration_date`
-   `status`
-   `created_by`
-   `created_at`
-   `updated_at`

### `guardians`

-   `guardian_id`
-   `school_id`
-   `full_name`
-   `mobile_number`
-   `email`
-   `status`
-   `created_at`

### `learner_guardians`

-   `learner_guardian_id`
-   `learner_id`
-   `guardian_id`
-   `relationship`
-   `is_primary_contact`
-   `can_receive_sms`
-   `portal_access_status`
-   `created_at`

### `academic_years`

-   `academic_year_id`
-   `school_id`
-   `year_name`
-   `start_date`
-   `end_date`
-   `status`

### `grades`

-   `grade_id`
-   `school_id`
-   `grade_name`
-   `education_stage`
-   `sequence_number`
-   `status`

### `classes`

-   `class_id`
-   `school_id`
-   `grade_id`
-   `class_name`
-   `status`

### `enrollments`

-   `enrollment_id`
-   `learner_id`
-   `academic_year_id`
-   `class_id`
-   `start_date`
-   `end_date`
-   `enrollment_status`
-   `previous_enrollment_id`
-   `created_by`
-   `approved_by`
-   `approval_date`
-   `reason`
-   creation/update timestamps

### `class_subjects`

-   `class_subject_id`
-   `class_id`
-   `subject_id`
-   `academic_year_id`
-   `status`

### `teacher_assignments`

-   `assignment_id`
-   `teacher_id`
-   `class_subject_id`
-   `assignment_type` (primary/backup as applicable)
-   `start_date`
-   `end_date`
-   `status`
-   `assigned_by`

## 15. Security, audit, and data integrity

The system must:

-   Enforce authorization on the server for every protected operation.
-   Prevent teachers from accessing unassigned classes or subjects.
-   Revoke sessions immediately when an account is deactivated.
-   Log important actions, including account and permission changes,
    attendance corrections, result changes, approvals,
    transfer/promotion decisions, sensitive exports, and SMS actions.
-   Retain prior values or history for corrections where required.
-   Preserve approved report cards and their versions.
-   Avoid routine deletion of learner history.
-   Protect sensitive learner-support notes and contact details.
-   Validate bulk imports before committing records.
-   Use transactions for operations that must succeed or fail as a
    unit.
-   Prevent duplicate daily attendance and conflicting active
    enrollments.
-   Recheck recipient eligibility and SMS budget before scheduled
    messages are sent.

## 16. Version 1 scope and deferred items

### In Version 1

-   Staff accounts and role-based permissions.
-   Learner registration, bulk import, profile, and history.
-   Classes, subjects, academic periods, and curriculum/grading
    configuration.
-   Enrollment, promotions, and transfers with approvals.
-   Daily attendance, absence early-warning workflow, and a dedicated
    absenteeism follow-up screen.
-   Numerical assessments and end-of-term competency assessment.
-   Report cards, draft PDF preview for teachers and Headteacher,
    approval, signatures, and historical versions.
-   SMS communication, verification, templates, scheduling, delivery
    history, and budget controls.
-   Reports, comparisons, exports, and audit trail.

### Deferred or not yet finalized

-   School fees.
-   Parent/guardian portal.
-   Specialized examination-class calculations.
-   Detailed approval rules for configuration changes in
    class/subject/curriculum management.
-   Technology stack and implementation plan.

## 17. Technology selection — not yet decided

No programming language, framework, database engine, hosting provider,
or deployment architecture has been selected. Do not treat any
previously discussed stack as final.

After the remaining screen decisions are closed, choose a stack that
supports:

-   Secure role-based access and auditability.
-   Relational data integrity and historical records.
-   Responsive web UI.
-   SMS gateway integration.
-   PDF and Excel exports/imports.
-   Scheduled tasks and notifications.
-   Backups and restore procedures.
-   Future multi-school tenancy.
-   Maintainability by the team that will support the system.

## 18. Remaining decisions and next steps

To finish requirements before selecting technologies:

1.  ~~Confirm whether to include a dedicated absenteeism follow-up
    screen.~~ **Done (v1.1):** included; see §6.
2.  ~~Decide whether teachers may preview/download draft report-card
    PDFs before submitting them to the Headteacher.~~ **Done (v1.1):**
    allowed, watermarked; see §9.
3.  Close any essential remaining screen decisions without reopening
    already-agreed requirements.
4.  Review and approve this requirements baseline.
5.  Finalize the relational database design, including relationships,
    constraints, and indexes.
6.  Compare suitable technology stacks and select one.
7.  Only then begin implementation planning and coding.

------------------------------------------------------------------------

**End of current requirements baseline.**
