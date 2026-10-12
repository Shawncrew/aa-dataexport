# aa-dataexport

Alliance Auth plugin that exposes selected data to other instances over a small read-only JSON API. Everything is managed in the AA admin: which endpoints are on, who has a key, and what each key may call. Only the fields each endpoint defines leave the instance; the database is never exposed.

## Endpoints

| Slug | Data |
|---|---|
| `afat` | FAT records: fleet name/type/doctrine, FC, character, corp/alliance at the time, system, ship. Requires `allianceauth-afat`. |
| `characters` | Main/alt associations: every claimed character with its main. Keyed on EVE character IDs; never includes AA user IDs, emails, Discord IDs or tokens. |

New endpoints are added in `dataexport/endpoints.py` with `@register(...)`; they appear in the admin automatically, **disabled** by default.

## Install

1. Add to `conf/requirements.txt`:
   ```text
   git+https://github.com/Shawncrew/aa-dataexport.git@main
   ```
2. Add to `INSTALLED_APPS` in `conf/local.py`:
   ```python
   INSTALLED_APPS += ["dataexport"]
   ```
3. Deploy (`aa.sh deploy`). The migration creates one disabled config row per endpoint.

Optional settings (`conf/local.py`):

```python
DATAEXPORT_RATE_LIMIT_PER_MINUTE = 120   # per key, 0 = off
DATAEXPORT_DEFAULT_PAGE_SIZE = 500
DATAEXPORT_MAX_PAGE_SIZE = 2000
DATAEXPORT_CLIENT_IP_HEADER = ""         # e.g. "HTTP_CF_CONNECTING_IP" behind Cloudflare
```

Set `DATAEXPORT_CLIENT_IP_HEADER` only if the origin is reachable solely through that proxy; otherwise the header can be spoofed and the per-key IP allowlist is meaningless. With it empty, `REMOTE_ADDR` is used, which behind Docker/NPM is the proxy's address.

## Admin workflow

1. **Data Export > Endpoints**: switch on the endpoints you want to offer. Optionally set *restrict alliance IDs* to limit the export to specific alliances (`afat`: alliance at the time of the FAT; `characters`: alliance of the owner's main).
2. **Data Export > API keys > Add**: name it after the consumer, tick the endpoints it may use, optionally set an expiry and an IP/CIDR allowlist. On save the key is shown **once**; only its SHA-256 is stored.
3. Revoke or regenerate from the key list actions. Regenerating invalidates the old key immediately. The list shows last-used time/IP and request count.

A key can call an endpoint only if the endpoint is enabled **and** granted to that key. Grant the `dataexport.*` admin permissions only to people you trust with this data.

## API

Base: `https://<your-auth>/dataexport/api/v1/`

Authenticate with `Authorization: Bearer <key>` (or `X-API-Key: <key>`). Query-string keys are deliberately not supported.

```bash
curl -H "Authorization: Bearer aadx_..." https://auth.example.org/dataexport/api/v1/            # what can I call?
curl -H "Authorization: Bearer aadx_..." "https://auth.example.org/dataexport/api/v1/characters/?limit=1000"
curl -H "Authorization: Bearer aadx_..." "https://auth.example.org/dataexport/api/v1/afat/?since=2026-09-01T00:00:00Z"
```

Responses are keyset-paginated so large syncs are cheap and stable:

```json
{ "endpoint": "characters", "count": 500, "next_after_id": 1873, "results": [ { "id": 1873, "...": "..." } ] }
```

Repeat the request with `after_id=<next_after_id>` until `next_after_id` is `null`. For incremental sync of AFAT, store the highest `id` you have and pass it as `after_id`.

| Status | Meaning |
|---|---|
| 401 | Missing, unknown, revoked, expired key, or disallowed IP (deliberately indistinguishable) |
| 403 | Valid key, endpoint not granted to it |
| 404 | Unknown or disabled endpoint |
| 400 | Bad `after_id` / `limit` / `since` |
| 429 | Rate limit exceeded |
| 503 | A required app (e.g. `afat`) isn't installed |

## Development

Tests use a minimal Django settings module and don't need a full Alliance Auth install:

```bash
pip install "django>=4.2"
DJANGO_SETTINGS_MODULE=tests.settings python -m django test tests
```

The `afat` and `characters` handlers query AFAT / Alliance Auth models and are not covered by these tests; verify them against a real instance after deploying (enable one, create a key, call it).
