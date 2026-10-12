# aa-dataexport

Alliance Auth plugin that exposes selected data to other instances over a small read-only JSON API. Everything is managed in the AA admin: which endpoints are on, who has a key, and what each key may call. Only the fields each endpoint defines leave the instance; the database is never exposed.

## Endpoints

| Slug | Data |
|---|---|
| `afat` | Flat FAT records, one row per pilot per fleet: fleet name/type/doctrine, FC, character, corp/alliance at the time, system, ship. Append-only, so `after_id` picks up late joiners. Requires `allianceauth-afat`. |
| `afat-fleets` | The same data **nested by fleet**: each fleet once (id, hash, name, type, doctrine, created, FC) with a `members` list (fat id, character, corp/alliance, system, ship) and `member_count`. `limit`/`after_id` count fleets (default 50, max 200). Fleets gain members after creation, so re-pull recent fleets with `since=` and upsert by `id`. Fleets with no (matching) members are omitted. |
| `characters` | Main/alt associations: every claimed character with its main. Keyed on EVE character IDs; never includes AA user IDs, emails, Discord IDs or tokens. |

New endpoints are added in `dataexport/endpoints.py` with `@register(...)`; they appear in the admin automatically, **disabled** by default.

## Install

1. Add to `conf/requirements.txt`:
   ```text
   git+https://github.com/Shawncrew/aa-dataexport.git@main
   ```
2. Add to `INSTALLED_APPS` in `conf/local.py`, **and** allow it to serve views without an AA login (Alliance Auth 5 requires this opt-in; without it every request is redirected to the login page). If you already have `APPS_WITH_PUBLIC_VIEWS`, add to it rather than redefining it:
   ```python
   INSTALLED_APPS += ["dataexport"]
   APPS_WITH_PUBLIC_VIEWS = ["dataexport"]
   ```
3. Deploy. In the Docker setup `aa.sh deploy` rebuilds and restarts but does not recreate containers on the new image, so also run `docker compose up -d --no-deps --force-recreate -t 5` for the web and worker services, then `manage.py migrate dataexport` if the deploy did not. The migration creates one disabled config row per endpoint.

Optional settings (`conf/local.py`):

```python
DATAEXPORT_RATE_LIMIT_PER_MINUTE = 120   # overall, per key across all endpoints, 0 = off
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

## Admin samples

The endpoint pages and the API guide show **real data**: the first 3 rows the endpoint would return, produced by the endpoint's own query with its alliance restriction applied, with long values trimmed and a hard size cap. If the live query errors or returns nothing, a fictional example is shown instead and the label says why (e.g. `live data unavailable (ImportError)`). Only users with admin access to the Data Export models see these.

## Throttling

Two layers, both per API key, counted in one-minute windows in the Django cache:

- **Per endpoint:** *Rate limit per minute* on each endpoint in the admin (default **30**, editable in the endpoints list; `0` = no endpoint limit). At 2000 rows per page that is up to 60,000 rows a minute, far more than a sync needs.
- **Overall:** `DATAEXPORT_RATE_LIMIT_PER_MINUTE` (default **120**) across all endpoints for the key.

Throttled requests get HTTP 429 with a `Retry-After` header. Endpoint responses carry `X-RateLimit-Limit` and `X-RateLimit-Remaining`. Requests that are rejected (401/403/404) don't use up an endpoint's budget. This protects against a runaway consumer; for volumetric abuse of unauthenticated traffic use Cloudflare/NPM rate limiting.

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
| 429 | Throttled. Wait `Retry-After` seconds. |
| 503 | A required app (e.g. `afat`) isn't installed |

## Development

Tests use a minimal Django settings module and don't need a full Alliance Auth install:

```bash
pip install "django>=4.2"
DJANGO_SETTINGS_MODULE=tests.settings python -m django test tests
```

The `afat` and `characters` handlers query AFAT / Alliance Auth models and are not covered by these tests; verify them against a real instance after deploying (enable one, create a key, call it).
