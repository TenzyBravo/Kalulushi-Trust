import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

# Tables that only the first prototype (before the approved design) created.
PROTOTYPE_MARKERS = ["people_student", "academics_enrollment"]
CONFIRM_VALUE = "drop-prototype-test-data"


class Command(BaseCommand):
    help = (
        "One-off: if the database still holds the pre-design prototype schema, stop the deploy, or drop it "
        f"when RESET_PROTOTYPE_DATABASE={CONFIRM_VALUE}. Does nothing on any other database."
    )

    def handle(self, *args, **options):
        tables = set(connection.introspection.table_names())
        if not tables & set(PROTOTYPE_MARKERS):
            return
        if os.environ.get("RESET_PROTOTYPE_DATABASE") != CONFIRM_VALUE:
            raise CommandError(
                "This database still has the old prototype tables, which the new design replaces. "
                "It held test data only. To clear it, set the environment variable "
                f"RESET_PROTOTYPE_DATABASE={CONFIRM_VALUE}, deploy once, then delete the variable."
            )
        with connection.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        self.stdout.write(self.style.WARNING("Dropped the prototype schema. Migrations will now build the new one."))
