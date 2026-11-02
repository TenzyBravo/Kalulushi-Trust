from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Staff and access"

    def ready(self):
        from . import signals  # noqa: F401
