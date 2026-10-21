from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        from .roles import sync_role_groups

        # post_migrate fires once per app, and permissions for apps listed after
        # accounts don't exist yet on our own turn, so re-sync after every app.
        post_migrate.connect(sync_role_groups, dispatch_uid="accounts.sync_role_groups")
