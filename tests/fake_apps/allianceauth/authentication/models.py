from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    main_character = models.ForeignKey(
        "eveonline.EveCharacter", null=True, on_delete=models.SET_NULL, related_name="+"
    )


class CharacterOwnership(models.Model):
    character = models.OneToOneField(
        "eveonline.EveCharacter", on_delete=models.CASCADE, related_name="character_ownership"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="character_ownerships"
    )
    owner_hash = models.CharField(max_length=28, default="")
