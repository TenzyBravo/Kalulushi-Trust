from django.contrib.postgres.operations import BtreeGistExtension, CITextExtension, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """PostgreSQL extensions the design relies on (design §2)."""

    initial = True
    dependencies = []

    operations = [
        BtreeGistExtension(),  # exclusion constraints on date ranges
        TrigramExtension(),  # likely-duplicate learner search
        CITextExtension(),  # case-insensitive usernames and emails
    ]
