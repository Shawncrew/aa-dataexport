"""Admin-facing documentation: sample requests and responses built from the registry."""
import json

from django.urls import reverse

KEY_PLACEHOLDER = "aadx_YOUR_KEY"


def base_url(request) -> str:
    return request.build_absolute_uri(reverse("dataexport:index"))


def curl_for(request, slug=None, query="", key=KEY_PLACEHOLDER) -> str:
    url = base_url(request) if slug is None else base_url(request) + f"{slug}/"
    if query:
        url += f"?{query}"
    return f'curl -s -H "Authorization: Bearer {key}" "{url}"'


def example_response(definition) -> str:
    rows = list(definition.example_rows)
    body = {
        "endpoint": definition.slug,
        "count": len(rows),
        "next_after_id": None,
        "results": rows,
    }
    return json.dumps(body, indent=2)


PAGING_NOTE = (
    "Responses are paged. When next_after_id is not null, repeat the request with "
    "after_id=<next_after_id> until it is null. All sample data above is fictional."
)
