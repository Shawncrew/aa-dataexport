from django.db import models
from django.utils import timezone


class EveCharacter(models.Model):
    character_id = models.BigIntegerField(unique=True)
    character_name = models.CharField(max_length=100)


class FatLink(models.Model):
    created = models.DateTimeField(default=timezone.now)
    fleet = models.CharField(max_length=254)
    hash = models.CharField(max_length=254, unique=True)
    character = models.ForeignKey(
        EveCharacter, null=True, on_delete=models.CASCADE, related_name="+"
    )
    fleet_type = models.CharField(max_length=254, blank=True, default="")
    doctrine = models.CharField(max_length=254, blank=True, default="")
    is_esilink = models.BooleanField(default=False)


class Fat(models.Model):
    character = models.ForeignKey(
        EveCharacter, on_delete=models.CASCADE, related_name="afat_fats"
    )
    corporation_eve_id = models.BigIntegerField(null=True)
    alliance_eve_id = models.BigIntegerField(null=True)
    fatlink = models.ForeignKey(
        FatLink, on_delete=models.CASCADE, related_name="afat_fats"
    )
    system = models.CharField(max_length=100, null=True)
    shiptype = models.CharField(max_length=100, null=True)

    class Meta:
        unique_together = (("character", "fatlink"),)
