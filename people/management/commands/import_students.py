from django.core.management.base import BaseCommand, CommandError

from people.importer import ImportFailed, import_students
from school.models import AcademicYear


class Command(BaseCommand):
    help = (
        "Import pupils and guardians from a CSV file. Every row is checked first; "
        "if any row has a problem nothing is saved."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file")
        parser.add_argument("--year", help="Academic year name for class enrolment (default: current year)")
        parser.add_argument("--dry-run", action="store_true", help="Check the file without saving anything")

    def handle(self, csv_file, year=None, dry_run=False, **options):
        academic_year = None
        if year:
            academic_year = AcademicYear.objects.filter(name=year).first()
            if academic_year is None:
                raise CommandError(f"Academic year '{year}' does not exist.")
        try:
            with open(csv_file, newline="", encoding="utf-8-sig") as fh:
                result = import_students(fh, year=academic_year, dry_run=dry_run)
        except FileNotFoundError:
            raise CommandError(f"File not found: {csv_file}")
        except ImportFailed as exc:
            for error in exc.errors:
                self.stderr.write(error)
            raise CommandError(str(exc))

        for warning in result.warnings:
            self.stderr.write(f"Warning: {warning}")
        if dry_run:
            self.stdout.write(
                f"Dry run OK: would create {result.created} pupil(s), updated {result.updated}."
            )
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Imported: created {result.created} pupil(s), updated {result.updated}, "
                f"new guardians {result.guardians_created}."
            ))
