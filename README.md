# Kalulushi Trust school management system

Built from `requirements-baseline.md` (v1.1) and the approved database design in
`docs/database-design-foundation.md`. Work is delivered one module at a time:

| Module | Status |
|---|---|
| 1. Foundation: structure, staff and roles, learners, guardians, enrolment, import, audit | this release |
| 2. Attendance and absenteeism follow-up | next |
| 3. Assessments, grading and competencies | |
| 4. Report cards | |
| 5. SMS to guardians | |
| 6. Reports, promotion/transfer requests, exports | |

## What module 1 does

- **Sign in** with staff number, username or email.
- **Four roles** (System Administrator, Headteacher, Academic Administrator, Teacher), defined in
  `accounts/roles.py`. A Headteacher creates only Teacher and Academic Administrator accounts (D6).
- **Teachers see only their classes**: subject assignments, class-teacher duty, or a live cover
  authorization. One function, `staffing/access.py`, decides this for every screen.
- **Deactivating** an account signs it out everywhere at once and ends its assignments and cover.
- **Learner numbers** like `KLT-0000018`: permanent, sequential, with a check digit that catches typing
  mistakes (D4).
- **Registration** warns about likely duplicates. Saving anyway needs a reason and the right role (D5).
  A guardian phone number already in use is suggested for linking, never merged automatically (D3).
- **Excel import**: template → upload → preview with problems and duplicates → confirm → error report.
- **Learner profile** with personal details, guardians and contacts (number history, verification) and
  enrolment history (transfer, correction). Later tabs fill in as their modules arrive.
- **Medical and support notes** are visible only to authorised staff, and every reading is logged.
- **Audit log** of every important change, written in the same transaction and append-only in the
  database.
- **School setup** (years, terms, stages, grades, classes, subjects) at `/setup/`. Every change is
  audited and nothing can be deleted.

## Deploy on Render

`build.sh` installs packages, collects static files, migrates and creates the first System
Administrator from `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD`.
Every push to `main` redeploys.

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Render's **Internal** Database URL |
| `DJANGO_SECRET_KEY` | generate in Render |
| `DJANGO_DEBUG` | `false` |
| `PYTHON_VERSION` | `3.12.7` |
| `SCHOOL_NAME`, `SCHOOL_CODE` | optional; default `Kalulushi Trust School`, `KLT`. Used only when the school is first created. |
| `RESET_PROTOTYPE_DATABASE` | one-off, see below |

**Upgrading from the first prototype.** The prototype's tables are replaced by the approved design. A
database that still has them stops the deploy with a message. Because it held test data only, set
`RESET_PROTOTYPE_DATABASE=drop-prototype-test-data`, deploy once, then delete the variable. This does
nothing on any other database.

**After the first deploy:** sign in, open **School setup → Academic years**, add the year with its terms,
select it and run *Make the selected year current*. Then add classes and class subjects, create staff
accounts, and assign teachers.

## Run locally

Needs Python 3.12+ and PostgreSQL 14+ (the `btree_gist`, `pg_trgm` and `citext` extensions ship with it).

```bash
pip install -r requirements.txt
createdb school_ms
export POSTGRES_DB=school_ms POSTGRES_USER=... POSTGRES_PASSWORD=... DJANGO_DEBUG=true
python manage.py migrate        # creates the school, Grades 1–7, Forms 1–4 and the four roles
python manage.py createsuperuser
python manage.py runserver
```

Import from the command line (checks only, unless `--confirm`):

```bash
python manage.py import_learners samples/learners.csv --as-user temwani
python manage.py import_learners samples/learners.csv --as-user temwani --confirm
```

## Tests

```bash
python manage.py test tests
```
