# school-ms

School back office: roles, school structure, pupils and their guardians.
Attendance, results and SMS/email delivery will build on this.

## Deploy on Render

The repo includes a Render Blueprint (`render.yaml`) that creates the web service
and a PostgreSQL database.

1. In Render: **New > Blueprint**, connect GitHub and pick this repository.
2. Fill in `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL` and
   `DJANGO_SUPERUSER_PASSWORD`. The first deploy creates that Admin account.
3. Click **Apply**. Each push to `main` redeploys: `build.sh` installs packages,
   collects static files and runs migrations.

The site is served at `https://<service-name>.onrender.com`. Free plans sleep when idle,
and Render's free database expires after a time limit, so move to paid plans before real data goes in.

## Run locally

Needs Python 3.12+ and PostgreSQL 14+.

```bash
pip install -r requirements.txt
createdb school_ms
export POSTGRES_DB=school_ms POSTGRES_USER=school POSTGRES_PASSWORD=... DJANGO_DEBUG=true
python manage.py migrate              # also creates the Admin / Head / Teacher groups
python manage.py seed_school --year 2026   # optional: Grades 1-12, a year with 3 terms, ECZ 1-9 scale
python manage.py createsuperuser      # superusers get the Admin role
python manage.py runserver
```

| Variable | Default |
|---|---|
| `DJANGO_SECRET_KEY` | required unless `DJANGO_DEBUG=true` |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` (Render's hostname is added automatically) |
| `DATABASE_URL` | unset; when set (Render does this) it replaces the `POSTGRES_*` variables |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` | `school_ms` / `school` / empty / `localhost` / `5432` |

## Roles

| Role | Django admin | Permissions |
|---|---|---|
| Admin | yes | add/change/delete/view all setup and people |
| Head | yes | view only |
| Teacher | no | staff pages only |

Roles live in `accounts/roles.py`. Groups are rebuilt on every `migrate`.

## Importing pupils

```bash
python manage.py import_students samples/students.csv --dry-run
python manage.py import_students samples/students.csv [--year 2026]
```

Required columns: `admission_number, first_name, last_name, gender, guardian_name, guardian_phone`.
Optional: `other_names, date_of_birth` (YYYY-MM-DD or DD/MM/YYYY), `class` (e.g. `4A`, must
exist in the year), `guardian_email, relationship`.

Every row is checked first; if any row has a problem nothing is saved and each problem
is listed by line number. Pupils are matched by admission number and guardians by phone,
so re-running a file updates instead of duplicating. A guardian's first link to a pupil
becomes the primary contact.

## Tests

```bash
python manage.py test tests
```
