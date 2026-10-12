from django.apps import AppConfig
from django.db.models.signals import post_migrate

from . import __version__


def _sync_after_migrate(sender, **kwargs):
    from .registry import sync_endpoint_configs

    sync_endpoint_configs()


class DataExportConfig(AppConfig):
    name = "dataexport"
    label = "dataexport"
    verbose_name = f"Data Export v{__version__}"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import endpoints  # noqa: F401  (registers the built-in endpoints)

        post_migrate.connect(_sync_after_migrate, sender=self)
