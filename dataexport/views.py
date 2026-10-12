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


def _throttle(bucket: str, limit: int):
    """Fixed one-minute window counter in the Django cache.

    Returns (allowed, remaining, retry_after_seconds). limit <= 0 means unlimited.
    """
    if limit <= 0:
        return True, None, 0
    now = int(timezone.now().timestamp())
    window = now // 60
    name = f"dataexport:rl:{bucket}:{window}"
    cache.add(name, 0, timeout=70)
    try:
        used = cache.incr(name)
    except ValueError:  # window expired between add and incr
        used = 1
    retry_after = max(1, 60 - now % 60)
    return used <= limit, max(0, limit - used), retry_after


def _too_many(retry_after, limit):
    response = _error("Rate limit exceeded", 429)
    response["Retry-After"] = str(retry_after)
    response["X-RateLimit-Limit"] = str(limit)
    response["X-RateLimit-Remaining"] = "0"
    return response


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
    limit = app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE
    allowed, _, retry_after = _throttle(f"key:{key.pk}", limit)
    if not allowed:
        return None, _too_many(retry_after, limit)
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
    allowed, remaining, retry_after = _throttle(
        f"key:{key.pk}:{slug}", config.rate_limit_per_minute
    )
    if not allowed:
        return _too_many(retry_after, config.rate_limit_per_minute)
    if definition.required_app and not apps.is_installed(definition.required_app):
        return _error(f"Required app '{definition.required_app}' is not installed", 503)

    try:
        after_id = _int_param(request, "after_id", 0)
        limit = _int_param(request, "limit", app_settings.DATAEXPORT_DEFAULT_PAGE_SIZE, 1)
        limit = min(limit, app_settings.DATAEXPORT_MAX_PAGE_SIZE)
        rows = definition.handler(request, config, after_id, limit)
    except ParamError as e:
        return _error(str(e), 400)

    response = _json(
        {
            "endpoint": slug,
            "count": len(rows),
            # Pass this as after_id for the next page; null once the end is reached.
            "next_after_id": rows[-1]["id"] if len(rows) == limit else None,
            "results": rows,
        }
    )
    if remaining is not None:
        response["X-RateLimit-Limit"] = str(config.rate_limit_per_minute)
        response["X-RateLimit-Remaining"] = str(remaining)
    return response

