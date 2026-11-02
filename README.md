# Kalulushi Trust school system

School back office. This is **stage 1, the foundation**, built from the approved design in
[docs/database-design-foundation.md](docs/database-design-foundation.md) and the
[requirements baseline](requirements-baseline.md). It covers:

- School structure: stages, Grades 1–7 and Forms 1–4, academic years and terms, classes per year, subjects
- Staff accounts with four roles, sign-in by staff number, username or email, session list and revocation, deactivation
- Teacher and class-teacher assignments, and time-limited cover
- Learner registration with permanent learner numbers (`KLT-0001234`), likely-duplicate warnings, guardians with phone history and verification, enrolment history (transfer, correction, leaving), restricted support notes, and CSV import
- An append-only audit log

Attendance, assessments, report cards and SMS come in later stages.

## Run locally

Needs Python 3.12+ and PostgreSQL 16.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
createdb school_ms
export POSTGRES_DB=school_ms POSTGRES_USER=school POSTGRES_PASSWORD=... DJANGO_DEBUG=true
python manage.py migrate                 # also creates the four role groups
python manage.py setup_school --year 2027   # school KLT, stages, grades, 2027 with three terms
python manage.py createsuperuser         # becomes a System Administrator
python manage.py runserver
```

Then, in **School setup** (the Django admin), add classes for the year, their subjects, and teacher assignments.

The database user that runs migrations needs rights to create the `btree_gist`, `pg_trgm` and `citext`
extensions, or have them created beforehand.

| Variable | Default |
|---|---|
| `DJANGO_SECRET_KEY` | required unless `DJANGO_DEBUG=true` |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` |
| `DJANGO_MEDIA_ROOT` | `./media` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` | `school_ms` / `school` / empty / `localhost` / `5432` |

## Where things are

| App | Holds |
|---|---|
| `core` | database extensions, shared fields, phone numbers, request context for the audit log |
| `school` | schools, settings, stages, grades, years, terms, classes, subjects |
| `accounts` | staff users, roles (`roles.py`), access rules (`access.py`), sessions, assignments, cover |
| `learners` | learners, numbers, duplicates, guardians, contacts, enrolments, support notes, import |
| `audit` | the audit log and the audited admin base class |

Every change that the design says must be audited goes through a function in a `services.py`,
which runs in one transaction and writes the audit record in that same transaction.

## Roles

| | System Administrator | Headteacher | Academic Administrator | Teacher |
|---|---|---|---|---|
| School structure, years, terms, subjects | manage | view | manage | – |
| Staff accounts | all roles | Teachers and Academic Administrators | view | – |
| Teacher assignments and cover | yes | yes | yes | – |
| Register and edit learners and guardians | yes | yes | yes | – |
| View learners | all | all | all | their classes only |
| Support notes | yes | yes | – | their classes, if granted individually |
| Audit log | yes | yes | – | – |

A teacher's classes come from active primary assignments, cover in force, and class-teacher
assignments. A backup assignment grants nothing until cover is authorized. Role permissions live in
`accounts/roles.py` and are re-applied on every `migrate`. No role can delete records.

## Importing learners

On the **Import from CSV** page, or:

```bash
python manage.py import_learners samples/learners.csv --as <username> --dry-run
python manage.py import_learners samples/learners.csv --as <username> [--year 2027]
```

Required columns: `first_name, last_name, gender, class, guardian_name, guardian_phone`.
Optional: `other_names, date_of_birth, registration_date` (YYYY-MM-DD or DD/MM/YYYY),
`previous_school, guardian_email, relationship`.

Each row is registered exactly as on screen: it gets a learner number, an enrolment, a primary
guardian and audit records. If any row has a problem, including a likely duplicate, nothing is
saved and every problem is listed by line. A guardian is reused only when both the name and the
phone number match an existing one.

## Tests

```bash
python manage.py test tests
```

The tests need PostgreSQL: most of the rules they check are database constraints. GitHub Actions
runs them on every push (`.github/workflows/tests.yml`).

## Deploy to Render

`render.yaml` describes the web app and a PostgreSQL 16 database, both in Frankfurt and both on
the free plan to start.

1. Merge into `main` (the Blueprint deploys `main`).
2. In the Render dashboard: **New → Blueprint**, choose this repository.
3. Fill in the variables it asks for: `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD`,
   `DJANGO_SUPERUSER_STAFF_NUMBER`, `DJANGO_SUPERUSER_FIRST_NAME`, `DJANGO_SUPERUSER_LAST_NAME`.
4. Apply. Each deploy runs `bin/build.sh`: install, collect static files, migrate, `setup_school`
   (school, grades, and the year in `SCHOOL_YEAR`), and create that first System Administrator if missing.
5. Sign in at the service's `onrender.com` address and change the administrator's password.

Before real learner data goes in: move the database off the free plan (free databases expire after
30 days and have no backups), and keep uploaded files (the school logo) on a persistent disk or
object storage, because the free web service's disk is wiped on every deploy.

## Production notes

- The audit log refuses `UPDATE` and `DELETE` through a trigger. For defence in depth, also revoke
  those rights on `audit_logs` from the application's database role.
- Set `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and run behind HTTPS (secure cookies are on when `DEBUG` is off).
