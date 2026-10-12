"""Code-defined export endpoints. Which are on, and who may call them, is data (see models)."""
from dataclasses import dataclass
from typing import Callable, Optional


class ParamError(Exception):
    """Raised by a handler for a bad query parameter (rendered as HTTP 400)."""


@dataclass(frozen=True)
class EndpointDefinition:
    slug: str
    title: str
    description: str
    # handler(request, config, after_id, limit) -> list[dict]; every row carries an "id"
    handler: Callable
    required_app: Optional[str] = None


_REGISTRY: dict[str, EndpointDefinition] = {}


def register(slug, title, description, required_app=None):
    def decorator(func):
        _REGISTRY[slug] = EndpointDefinition(slug, title, description, func, required_app)
        return func

    return decorator


def get_definition(slug) -> Optional[EndpointDefinition]:
    return _REGISTRY.get(slug)


def all_definitions() -> list[EndpointDefinition]:
    return list(_REGISTRY.values())


def sync_endpoint_configs():
    """Make sure every registered endpoint has a config row (disabled by default)."""
    from .models import EndpointConfig

    for slug in _REGISTRY:
        EndpointConfig.objects.get_or_create(slug=slug)
