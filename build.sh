#!/usr/bin/env bash
# Render build step: install, collect static files, migrate.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py reset_prototype_schema
python manage.py migrate --no-input
# Zambia's public holidays for the current and planned years; only missing ones are added.
python manage.py seed_holidays

# First System Administrator, created once from DJANGO_SUPERUSER_* env vars if they are set.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  python manage.py createsuperuser --no-input || echo "Superuser already exists; skipping."
fi
