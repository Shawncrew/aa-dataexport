import logging

from django.apps import apps
from django.core.cache import cache
from django.db.models import F
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import app_settings, security
from .models import ApiKey, EndpointConfig
from .registry import ParamError, get_definition

logger = logging.getLogger(__name__)


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _error(message, status):
    return _json({"error": message}, status=status)


def _authenticate(request, ip):
    raw = security.extract_key(request)
    if not raw:
        return None
    key = ApiKey.objects.filter(key_hash=security.hash_key(raw)).first()
    if key is None or not key.is_active or key.is_expired:
        return None
    if not security.ip_allowed(key.allowed_ips, ip):
        logger.warning("dataexport: key %s used from disallowed IP %s", key.key_prefix, ip)
        return None
    return key


def _rate_limited(key) -> bool:
    limit = app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return False
    bucket = f"dataexport:rl:{key.pk}:{int(timezone.now().timestamp() // 60)}"
    cache.add(bucket, 0, timeout=70)
    try:
        return cache.incr(bucket) > limit
    except ValueError:  # bucket expired between add and incr
        return False


def _touch(key, ip):
    ApiKey.objects.filter(pk=key.pk).update(
        last_used_at=timezone.now(),
        last_used_ip=ip,
        request_count=F("request_count") + 1,
    )


def _int_param(request, name, default, minimum=0):
    raw = request.GET.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ParamError(f"{name} must be an integer")
    if value < minimum:
        raise ParamError(f"{name} must be >= {minimum}")
    return value


def _authorised(request):
    """Return (key, None) or (None, error_response). Unauthenticated is always 401."""
    ip = security.client_ip(request)
    key = _authenticate(request, ip)
    if key is None:
        return None, _error("Invalid or unauthorised API key", 401)
    if _rate_limited(key):
        return None, _error("Rate limit exceeded", 429)
    _touch(key, ip)
    return key, None


@require_GET
def index(request):
    """List the endpoints this key may call."""
    key, err = _authorised(request)
    if err:
        return err
    out = []
    for config in key.allowed_endpoints.filter(enabled=True):
        definition = get_definition(config.slug)
        if definition is None:
            continue
        out.append(
            {
                "slug": definition.slug,
                "title": definition.title,
                "description": definition.description,
                "url": request.build_absolute_uri(
                    reverse("dataexport:endpoint", args=[definition.slug])
                ),
            }
        )
    return _json({"endpoints": out})


@require_GET
def endpoint(request, slug):
    key, err = _authorised(request)
    if err:
        return err

    definition = get_definition(slug)
    config = EndpointConfig.objects.filter(slug=slug).first() if definition else None
    if config is None or not config.enabled:
        return _error("Not found", 404)
    if not key.allowed_endpoints.filter(pk=config.pk).exists():
        return _error("This key is not permitted to use this endpoint", 403)
    if definition.required_app and not apps.is_installed(definition.required_app):
        return _error(f"Required app '{definition.required_app}' is not installed", 503)

    try:
        after_id = _int_param(request, "after_id", 0)
        limit = _int_param(request, "limit", app_settings.DATAEXPORT_DEFAULT_PAGE_SIZE, 1)
        limit = min(limit, app_settings.DATAEXPORT_MAX_PAGE_SIZE)
        rows = definition.handler(request, config, after_id, limit)
    except ParamError as e:
        return _error(str(e), 400)

    return _json(
        {
            "endpoint": slug,
            "count": len(rows),
            # Pass this as after_id for the next page; null once the end is reached.
            "next_after_id": rows[-1]["id"] if len(rows) == limit else None,
            "results": rows,
        }
    )

