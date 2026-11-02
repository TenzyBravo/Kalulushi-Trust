#!/usr/bin/env bash
# Render build step: install, collect static files, migrate, and make sure the school
# and first System Administrator exist. Safe to run on every deploy.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py setup_school --name "${SCHOOL_NAME:-Kalulushi Trust School}" --code "${SCHOOL_CODE:-KLT}" \
  ${SCHOOL_YEAR:+--year "$SCHOOL_YEAR"}

# First System Administrator, from DJANGO_SUPERUSER_* variables. Skipped once the account exists.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  python manage.py createsuperuser --no-input || echo "Administrator account already exists."
fi
