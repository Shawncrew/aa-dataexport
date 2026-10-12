"""Admin-facing documentation: sample requests and responses built from the registry."""
import json

from django.urls import reverse

from . import app_settings

KEY_PLACEHOLDER = "aadx_YOUR_KEY"


def base_url(request) -> str:
    path = reverse("dataexport:index")
    if app_settings.DATAEXPORT_PUBLIC_BASE_URL:
        return app_settings.DATAEXPORT_PUBLIC_BASE_URL + path
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    if proto in ("http", "https"):
        return f"{proto}://{request.get_host()}{path}"
    return request.build_absolute_uri(path)


def curl_for(request, slug=None, query="", key=KEY_PLACEHOLDER) -> str:
    url = base_url(request) if slug is None else base_url(request) + f"{slug}/"
    if query:
        url += f"?{query}"
    return f'curl -s -H "Authorization: Bearer {key}" "{url}"'


SAMPLE_ROWS = 3  # real rows shown in the admin
SAMPLE_LIST_ITEMS = 3  # nested lists (e.g. fleet members) are cut to this many items
MAX_VALUE_CHARS = 80  # long strings (e.g. fleet names) are trimmed in the sample
MAX_SAMPLE_CHARS = 6000  # hard cap on the rendered sample


def _body(slug, rows, limit=None):
    return {
        "endpoint": slug,
        "count": len(rows),
        "next_after_id": rows[-1]["id"] if rows and limit and len(rows) == limit else None,
        "results": rows,
    }


def example_response(definition) -> str:
    """Fictional sample, used when no real data can be shown."""
    return json.dumps(_body(definition.slug, list(definition.example_rows)), indent=2)


def _trim(value, cut_lists):
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[: MAX_VALUE_CHARS - 1] + "..."
    if isinstance(value, dict):
        return {k: _trim(v, cut_lists) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > SAMPLE_LIST_ITEMS:
            cut_lists.append(True)
        return [_trim(v, cut_lists) for v in value[:SAMPLE_LIST_ITEMS]]
    return value


def sample_response(definition, config) -> dict:
    """Real data as the API would return it, limited to a few rows.

    Runs the endpoint's own handler with the endpoint's config (so alliance restriction
    applies). Falls back to the fictional example if that fails or returns nothing.
    Returns {"text", "live", "label"}.
    """
    from django.test import RequestFactory

    fallback = None
    try:
        rows = definition.handler(RequestFactory().get("/"), config, 0, SAMPLE_ROWS)
        cut_lists = []
        rows = [{k: _trim(v, cut_lists) for k, v in row.items()} for row in rows]
        if rows:
            text = json.dumps(_body(definition.slug, rows, SAMPLE_ROWS), indent=2)
            if len(text) > MAX_SAMPLE_CHARS:
                text = text[:MAX_SAMPLE_CHARS] + "\n... (truncated)"
            return {
                "text": text,
                "live": True,
                "label": f"real data, first {SAMPLE_ROWS} rows only"
                + (f", lists cut to {SAMPLE_LIST_ITEMS} items" if cut_lists else ""),
            }
        fallback = "no rows to show yet"
    except Exception as exc:  # noqa: BLE001 - docs must never break the admin page
        fallback = f"live data unavailable ({type(exc).__name__})"
    return {
        "text": example_response(definition),
        "live": False,
        "label": f"fictional example - {fallback}",
    }


PAGING_NOTE = (
    "Responses are paged. When next_after_id is not null, repeat the request with "
    "after_id=<next_after_id> until it is null. Requests are throttled per key (see the "
    "rate limit column); on HTTP 429 wait Retry-After seconds. Every response carries "
    "X-RateLimit-Limit / X-RateLimit-Remaining. Samples show the first few real rows (long values trimmed), or a fictional example when there is no data yet."
)
