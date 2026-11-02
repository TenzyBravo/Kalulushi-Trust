from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        from . import signals  # noqa: F401
        from .roles import sync_role_groups

        # post_migrate fires once per app; permissions for apps after this one
        # don't exist on our own turn, so re-sync after every app.
        post_migrate.connect(sync_role_groups, dispatch_uid="accounts.sync_role_groups")
