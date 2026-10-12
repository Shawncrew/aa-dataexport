from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from . import security
from .registry import get_definition


def parse_id_list(text: str) -> list[int]:
    out = []
    for part in (text or "").replace("\n", ",").split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


class EndpointConfig(models.Model):
    """One row per code-defined endpoint: whether it is switched on, plus optional scoping."""

    slug = models.SlugField(max_length=64, unique=True, editable=False)
    enabled = models.BooleanField(
        default=False, help_text="Disabled endpoints return 404 to every key."
    )
    restrict_alliance_ids = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Comma separated alliance IDs. If set, only data for these alliances is "
            "exported (AFAT: alliance at the time of the FAT; characters: alliance of "
            "the owning user's main). Blank = no restriction."
        ),
    )
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        verbose_name = "endpoint"

    def __str__(self):
        d = get_definition(self.slug)
        return d.title if d else self.slug

    def clean(self):
        try:
            parse_id_list(self.restrict_alliance_ids)
        except ValueError:
            raise ValidationError(
                {"restrict_alliance_ids": "Must be comma separated integers."}
            )

    @property
    def alliance_ids(self) -> list[int]:
        return parse_id_list(self.restrict_alliance_ids)


class ApiKey(models.Model):
    """A credential for one consumer. Only the SHA-256 of the key is stored."""

    name = models.CharField(
        max_length=100, unique=True, help_text="Who/what this key is for."
    )
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    key_prefix = models.CharField(max_length=16, editable=False)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Blank = never.")
    allowed_endpoints = models.ManyToManyField(
        EndpointConfig,
        blank=True,
        related_name="api_keys",
        help_text="Endpoints this key may call. The endpoint must also be enabled.",
    )
    allowed_ips = models.TextField(
        blank=True,
        help_text="Optional. One IP or CIDR per line. Blank = any address.",
    )
    notes = models.TextField(blank=True)

    created = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True, editable=False)
    request_count = models.BigIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "API key"

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"

    def clean(self):
        try:
            security.parse_networks(self.allowed_ips)
        except ValueError as e:
            raise ValidationError({"allowed_ips": f"Invalid IP/CIDR: {e}"})

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def regenerate(self) -> str:
        """Replace the key material and return the new raw key (shown once, not stored)."""
        raw, self.key_hash, self.key_prefix = security.generate_key()
        return raw
