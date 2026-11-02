from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from core.models import School
from learners import importer


class Command(BaseCommand):
    help = "Validate (and optionally import) learners from the official template. Nothing is saved without --confirm."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--as-user", required=True, help="Username of the staff member doing the import (audited)")
        parser.add_argument("--confirm", action="store_true", help="Create the valid rows")
        parser.add_argument("--override-reason", default="", help="Import likely duplicates too, with this reason")

    def handle(self, file, as_user, confirm, override_reason, **opts):
        try:
            actor = User.objects.get(username=as_user, is_active=True)
        except User.DoesNotExist:
            raise CommandError(f"No active user '{as_user}'.")
        try:
            with open(file, "rb") as fh:
                rows = importer.read_upload(fh)
        except (OSError, importer.ImportFileError) as exc:
            raise CommandError(str(exc))
        school = School.current()
        preview = importer.validate(rows, school)
        for row in preview.invalid:
            self.stderr.write(f"Line {row.line}: {'; '.join(row.errors)}")
        for row in preview.flagged:
            for number, name, why in row.duplicates:
                self.stderr.write(f"Line {row.line}: likely duplicate of {name} {number} ({why})")
        self.stdout.write(f"{len(preview.rows)} row(s): {len(preview.valid)} valid "
                          f"({len(preview.flagged)} flagged as likely duplicates), {len(preview.invalid)} invalid.")
        if not confirm:
            self.stdout.write("Checked only. Nothing was saved; add --confirm to import the valid rows.")
            return
        result = importer.confirm(rows, school, actor, override_reason=override_reason)
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(result.created)} learner(s); skipped {len(result.skipped_duplicates)} likely duplicate(s) "
            f"and {len(result.invalid)} invalid row(s)."))
