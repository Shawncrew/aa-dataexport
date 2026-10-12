from django.conf import settings

# Requests per API key per minute; 0 disables rate limiting.
DATAEXPORT_RATE_LIMIT_PER_MINUTE = int(
    getattr(settings, "DATAEXPORT_RATE_LIMIT_PER_MINUTE", 120)
)

DATAEXPORT_DEFAULT_PAGE_SIZE = int(getattr(settings, "DATAEXPORT_DEFAULT_PAGE_SIZE", 500))
DATAEXPORT_MAX_PAGE_SIZE = int(getattr(settings, "DATAEXPORT_MAX_PAGE_SIZE", 2000))

# request.META key holding the real client IP, used for per-key IP allowlists and
# the "last used from" field. Empty = REMOTE_ADDR. Behind Cloudflare use
# "HTTP_CF_CONNECTING_IP". Only set this if the origin is reachable solely through
# that proxy, otherwise clients can spoof it.
DATAEXPORT_CLIENT_IP_HEADER = (
    getattr(settings, "DATAEXPORT_CLIENT_IP_HEADER", "") or ""
).strip()

# Public origin used in the admin's example curl commands, e.g. "https://auth.example.org".
# Blank = derive from the request (honours X-Forwarded-Proto). Set it if the examples show
# http:// because the proxy chain doesn't forward the original scheme.
DATAEXPORT_PUBLIC_BASE_URL = (
    getattr(settings, "DATAEXPORT_PUBLIC_BASE_URL", "") or ""
).strip().rstrip("/")
