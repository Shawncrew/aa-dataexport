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
    "characters",
    "Main/alt associations",
    "Every claimed character with the main it belongs to. Keyed on EVE character IDs; "
    "no AA user IDs, emails or Discord IDs. Params: after_id, limit.",
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
