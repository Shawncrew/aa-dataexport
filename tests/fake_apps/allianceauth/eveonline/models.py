from django.db import models


class EveCharacter(models.Model):
    character_id = models.BigIntegerField(unique=True)
    character_name = models.CharField(max_length=100)
    corporation_id = models.BigIntegerField(null=True)
    corporation_name = models.CharField(max_length=100, blank=True, default="")
    alliance_id = models.BigIntegerField(null=True)
    alliance_name = models.CharField(max_length=100, null=True, blank=True)
