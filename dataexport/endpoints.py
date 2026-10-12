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
    "afat",
    "AFAT participation",
    "FAT records (who flew in which fleet): fleet name/type/doctrine, FC, character, "
    "corp/alliance at the time, system and ship. Params: after_id, limit, since "
    "(fleet created at or after).",
    required_app="afat",
    example_query="limit=100&since=2026-10-01T00:00:00Z",
    example_rows=[
        {
            "id": 48211,
            "fatlink_hash": "9f3c0d1e5a7b44c2",
            "fleet_name": "Strat op - Keepstar defence",
            "fleet_type": "Strategic",
            "doctrine": "Muninn",
            "fleet_created": "2026-10-10T19:02:11+00:00",
            "fc_character_id": 90000001,
            "fc_character_name": "Example FC",
            "character_id": 90000002,
            "character_name": "Example Pilot",
            "corporation_id": 98000001,
            "alliance_id": 99000001,
            "system": "1DQ1-A",
            "ship_type": "Muninn",
        },
        {
            "id": 48212,
            "fatlink_hash": "9f3c0d1e5a7b44c2",
            "fleet_name": "Strat op - Keepstar defence",
            "fleet_type": "Strategic",
            "doctrine": "Muninn",
            "fleet_created": "2026-10-10T19:02:11+00:00",
            "fc_character_id": 90000001,
            "fc_character_name": "Example FC",
            "character_id": 90000003,
            "character_name": "Another Pilot",
            "corporation_id": 98000001,
            "alliance_id": 99000001,
            "system": "1DQ1-A",
            "ship_type": "Scimitar",
        },
    ],
)
def afat(request, config, after_id, limit):
    from afat.models import Fat

    qs = Fat.objects.select_related("character", "fatlink", "fatlink__character")
    qs = qs.filter(pk__gt=after_id)
    since = _parse_since(request)
    if since:
        qs = qs.filter(fatlink__created__gte=since)
    if config.alliance_ids:
        qs = qs.filter(alliance_eve_id__in=config.alliance_ids)

    rows = []
    for fat in qs.order_by("pk")[:limit]:
        link = fat.fatlink
        fc = link.character
        rows.append(
            {
                "id": fat.pk,
                "fatlink_hash": link.hash,
                "fleet_name": link.fleet,
                "fleet_type": link.fleet_type,
                "doctrine": link.doctrine,
                "fleet_created": link.created.isoformat(),
                "fc_character_id": fc.character_id if fc else None,
                "fc_character_name": fc.character_name if fc else None,
                "character_id": fat.character.character_id,
                "character_name": fat.character.character_name,
                "corporation_id": fat.corporation_eve_id,
                "alliance_id": fat.alliance_eve_id,
                "system": fat.system,
                "ship_type": fat.shiptype,
            }
        )
    return rows


@register(
    "afat-fleets",
    "AFAT fleets (nested)",
    "One entry per fleet (FAT link) with its members nested inside: fleet name, hash, type, "
    "doctrine, FC, and each member's character, corp/alliance, system and ship. limit and "
    "after_id count fleets, not members. Fleets keep gaining members after they are "
    "created, so re-pull recent fleets with since= and upsert by id. Params: after_id, "
    "limit, since (fleet created at or after).",
    required_app="afat",
    default_limit=50,
    max_limit=200,
    example_query="limit=50&since=2026-10-01T00:00:00Z",
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


@register(
    "characters",
    "Main/alt associations",
    "Every claimed character with the main it belongs to. Keyed on EVE character IDs; "
    "no AA user IDs, emails or Discord IDs. Params: after_id, limit.",
    example_query="limit=500",
    example_rows=[
        {
            "id": 101,
            "character_id": 90000010,
            "character_name": "Example Main",
            "corporation_id": 98000001,
            "corporation_name": "Example Corp",
            "alliance_id": 99000001,
            "alliance_name": "Example Alliance",
            "main_character_id": 90000010,
            "main_character_name": "Example Main",
            "is_main": True,
        },
        {
            "id": 102,
            "character_id": 90000011,
            "character_name": "Example Alt One",
            "corporation_id": 98000002,
            "corporation_name": "Example Alt Corp",
            "alliance_id": None,
            "alliance_name": None,
            "main_character_id": 90000010,
            "main_character_name": "Example Main",
            "is_main": False,
        },
    ],
)
def characters(request, config, after_id, limit):
    from allianceauth.authentication.models import CharacterOwnership

    qs = CharacterOwnership.objects.select_related(
        "character", "user__profile__main_character"
    ).filter(pk__gt=after_id)
    if config.alliance_ids:
        qs = qs.filter(
            user__profile__main_character__alliance_id__in=config.alliance_ids
        )

    rows = []
    for own in qs.order_by("pk")[:limit]:
        char = own.character
        profile = getattr(own.user, "profile", None)
        main = profile.main_character if profile else None
        rows.append(
            {
                "id": own.pk,
                "character_id": char.character_id,
                "character_name": char.character_name,
                "corporation_id": char.corporation_id,
                "corporation_name": char.corporation_name,
                "alliance_id": char.alliance_id,
                "alliance_name": char.alliance_name,
                "main_character_id": main.character_id if main else None,
                "main_character_name": main.character_name if main else None,
                "is_main": bool(main and main.character_id == char.character_id),
            }
        )
    return rows
