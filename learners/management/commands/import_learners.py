from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.context import acting_as
from learners.importer import ImportFailed, import_learners
from school.models import AcademicYear


class Command(BaseCommand):
    help = "Register learners from a CSV file. Nothing is saved unless every row is valid."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--as", dest="username", required=True, help="Staff username recorded as registering them.")
        parser.add_argument("--year", help="Academic year name, e.g. 2027. Defaults to the current year.")
        parser.add_argument("--dry-run", action="store_true", help="Check the file without saving anything.")
        parser.add_argument("--override-reason", default="", help="Save likely duplicates anyway, for this reason.")

    def handle(self, csv_path, username, year, dry_run, override_reason, **options):
        try:
            actor = get_user_model().objects.get(username=username, is_active=True)
        except get_user_model().DoesNotExist:
            raise CommandError(f"No active staff account '{username}'.") from None
        academic_year = None
        if year:
            academic_year = AcademicYear.objects.filter(school=actor.school, name=year).first()
            if academic_year is None:
                raise CommandError(f"No academic year '{year}'.")

        with open(csv_path, newline="", encoding="utf-8-sig") as f, acting_as(actor):
            try:
                result = import_learners(
                    actor, f, year=academic_year, dry_run=dry_run, override_reason=override_reason
                )
            except ImportFailed as exc:
                for error in exc.errors:
                    self.stderr.write(error)
                raise CommandError(str(exc)) from None

        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(warning))
        verb = "Would register" if dry_run else "Registered"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(result.learners)} learner(s)."))
        if not dry_run:
            for learner in result.learners:
                self.stdout.write(f"  {learner.learner_number}  {learner.full_name}")
