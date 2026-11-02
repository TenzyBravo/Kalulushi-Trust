from django.apps import AppConfig
from django.db.models.signals import post_migrate


class LearnersConfig(AppConfig):
    name = "learners"
    verbose_name = "Learners"

    def ready(self):
        from accounts.roles import sync_role_groups

        # Last of our apps to migrate, so every permission the roles name exists by now.
        post_migrate.connect(sync_role_groups, sender=self, dispatch_uid="sync_role_groups")
