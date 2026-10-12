"""Built-in export endpoints. Only the fields listed here ever leave the instance."""
from datetime import datetime, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .registry import ParamError, register


def _parse_since(request):
    raw = request.GET.get("since")
    if not raw:
        return None
    try:
        value = datetime.fromtimestamp(int(raw), tz=dt_timezone.utc)
    except (ValueError, OverflowError, OSError):
        try:
            value = parse_datetime(raw)
        except ValueError:
            value = None
    if value is None:
        raise ParamError("since must be ISO 8601 or a unix timestamp")
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt_timezone.utc)
    return value


@register(
    "afat-fleets",
    "AFAT fleets (nested)",
    "One entry per fleet (FAT link) with its members nested inside: fleet name, hash, type, "
    "doctrine, FC, and each member's character, corp/alliance, system and ship. limit and "
    "after_id count fleets, not members. Fleets keep gaining members after they are "
    "created, so re-pull recent fleets with since= and upsert by id. Params: after_id, "
    "limit, since (fleet created at or after).",
    required_app="afat",
    default_limit=1000,
    max_limit=1000,
    example_query="limit=1000&since=2026-10-01T00:00:00Z",
    example_rows=[
        {
            "id": 912,
            "hash": "9f3c0d1e5a7b44c2",
            "fleet_name": "Strat op - Keepstar defence",
            "fleet_type": "Strategic",
            "doctrine": "Muninn",
            "created": "2026-10-10T19:02:11+00:00",
            "is_esi_link": True,
            "fc": {"character_id": 90000001, "character_name": "Example FC"},
            "member_count": 2,
            "members": [
                {
                    "fat_id": 48211,
                    "character_id": 90000002,
                    "character_name": "Example Pilot",
                    "corporation_id": 98000001,
                    "alliance_id": 99000001,
                    "system": "1DQ1-A",
                    "ship_type": "Muninn",
                },
                {
                    "fat_id": 48212,
                    "character_id": 90000003,
                    "character_name": "Another Pilot",
                    "corporation_id": 98000001,
                    "alliance_id": 99000001,
                    "system": "1DQ1-A",
                    "ship_type": "Scimitar",
                },
            ],
        },
    ],
)
def afat_fleets(request, config, after_id, limit):
    from afat.models import Fat, FatLink
    from django.db.models import Exists, OuterRef, Prefetch

    # With an alliance restriction only matching members are exported, and fleets with
    # no matching member are excluded in the query so paging by fleet id stays correct.
    member_filter = {}
    if config.alliance_ids:
        member_filter["alliance_eve_id__in"] = config.alliance_ids

    fleets = FatLink.objects.filter(pk__gt=after_id).filter(
        Exists(Fat.objects.filter(fatlink=OuterRef("pk"), **member_filter))
    )
    since = _parse_since(request)
    if since:
        fleets = fleets.filter(created__gte=since)
    fleets = (
        fleets.select_related("character")
        .prefetch_related(
            Prefetch(
                "afat_fats",
                queryset=Fat.objects.filter(**member_filter)
                .select_related("character")
                .order_by("pk"),
            )
        )
        .order_by("pk")[:limit]
    )

    rows = []
    for link in fleets:
        fc = link.character
        members = [
            {
                "fat_id": fat.pk,
                "character_id": fat.character.character_id,
                "character_name": fat.character.character_name,
                "corporation_id": fat.corporation_eve_id,
                "alliance_id": fat.alliance_eve_id,
                "system": fat.system,
                "ship_type": fat.shiptype,
            }
            for fat in link.afat_fats.all()
        ]
        rows.append(
            {
                "id": link.pk,
                "hash": link.hash,
                "fleet_name": link.fleet,
                "fleet_type": link.fleet_type,
                "doctrine": link.doctrine,
                "created": link.created.isoformat(),
                "is_esi_link": link.is_esilink,
                "fc": (
                    {"character_id": fc.character_id, "character_name": fc.character_name}
                    if fc
                    else None
                ),
                "member_count": len(members),
                "members": members,
            }
        )
    return rows


def _char_summary(char):
    return {
        "character_id": char.character_id,
        "character_name": char.character_name,
        "corporation_id": char.corporation_id,
        "corporation_name": char.corporation_name,
        "alliance_id": char.alliance_id,
        "alliance_name": char.alliance_name,
    }


@register(
    "mains",
    "Mains with their alts (nested)",
    "One entry per main character with that main's alts nested inside. Keyed on EVE "
    "character IDs (id = the main's character ID); no AA user IDs, emails or Discord IDs. "
    "There is no change timestamp, so sync by paging through everything and treating the "
    "result as a full snapshot. limit and after_id count mains. Params: after_id, limit.",
    default_limit=20000,
    max_limit=20000,
    example_query="limit=20000",
    example_rows=[
        {
            "id": 90000010,
            "main": {
                "character_id": 90000010,
                "character_name": "Example Main",
                "corporation_id": 98000001,
                "corporation_name": "Example Corp",
                "alliance_id": 99000001,
                "alliance_name": "Example Alliance",
            },
            "alts": [
                {
                    "character_id": 90000011,
                    "character_name": "Example Alt One",
                    "corporation_id": 98000002,
                    "corporation_name": "Example Alt Corp",
                    "alliance_id": None,
                    "alliance_name": None,
                },
                {
                    "character_id": 90000012,
                    "character_name": "Example Alt Two",
                    "corporation_id": 98000001,
                    "corporation_name": "Example Corp",
                    "alliance_id": 99000001,
                    "alliance_name": "Example Alliance",
                },
            ],
            "character_count": 3,
        },
    ],
)
def mains(request, config, after_id, limit):
    from allianceauth.authentication.models import CharacterOwnership, UserProfile
    from django.db.models import Prefetch

    # Cursor is the main's EVE character ID, so no AA-internal id is exposed.
    profiles = UserProfile.objects.filter(
        main_character__isnull=False, main_character__character_id__gt=after_id
    )
    if config.alliance_ids:
        profiles = profiles.filter(main_character__alliance_id__in=config.alliance_ids)
    profiles = (
        profiles.select_related("user", "main_character")
        .prefetch_related(
            Prefetch(
                "user__character_ownerships",
                queryset=CharacterOwnership.objects.select_related("character").order_by(
                    "character__character_id"
                ),
            )
        )
        .order_by("main_character__character_id")[:limit]
    )

    rows = []
    for profile in profiles:
        main = profile.main_character
        alts = [
            _char_summary(own.character)
            for own in profile.user.character_ownerships.all()
            if own.character.character_id != main.character_id
        ]
        rows.append(
            {
                "id": main.character_id,
                "main": _char_summary(main),
                "alts": alts,
                "character_count": len(alts) + 1,
            }
        )
    return rows
