import hashlib
import ipaddress
import secrets

from . import app_settings

KEY_PREFIX = "aadx_"
DISPLAY_PREFIX_LEN = 12


def hash_key(raw: str) -> str:
    # Keys are 256-bit random tokens, so a plain SHA-256 is sufficient (no need for a
    # slow password hash) and allows an indexed lookup.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Return (raw_key, sha256_hash, display_prefix). The raw key is never stored."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:DISPLAY_PREFIX_LEN]


def extract_key(request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def client_ip(request) -> str | None:
    header = app_settings.DATAEXPORT_CLIENT_IP_HEADER
    value = request.META.get(header, "") if header else request.META.get("REMOTE_ADDR", "")
    # Header values may be a comma separated chain; the first entry is the client.
    value = value.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def parse_networks(text: str) -> list:
    """Parse one-IP-or-CIDR-per-line text. Raises ValueError on a bad line."""
    nets = []
    for line in (text or "").replace(",", "\n").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            nets.append(ipaddress.ip_network(line, strict=False))
    return nets


def ip_allowed(text: str, ip: str | None) -> bool:
    nets = parse_networks(text)
    if not nets:
        return True
    if ip is None:
        return False
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in nets)
