"""The nested `mains` handler against a stand-in AA schema."""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter
from dataexport import docs, registry
from dataexport.models import ApiKey, EndpointConfig

User = get_user_model()


def char(cid, name, corp=5, alliance=10):
    return EveCharacter.objects.create(
        character_id=cid, character_name=name, corporation_id=corp,
        corporation_name=f"Corp {corp}", alliance_id=alliance,
        alliance_name=f"Alliance {alliance}" if alliance else None,
    )


def account(username, main, alts=(), has_main=True):
    user = User.objects.create_user(username, f"{username}@secret.example", "pw")
    for c in (main, *alts):
        CharacterOwnership.objects.create(character=c, user=user, owner_hash=f"h{c.pk}")
    UserProfile.objects.create(user=user, main_character=main if has_main else None)
    return user


class MainAltTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        registry.sync_endpoint_configs()
        # Alliance 10: main 200 with alts 201 (no alliance), 202; main 100 with no alts
        account("u1", char(200, "Main Two"), [char(201, "Alt 2a", corp=6, alliance=None), char(202, "Alt 2b")])
        account("u2", char(100, "Main One"))
        # Alliance 20 main with an alt that sits in alliance 10
        account("u3", char(300, "Main Three", alliance=20), [char(301, "Alt 3a", alliance=10)])
        # Account with owned characters but no main set
        account("u4", char(400, "Nomain"), has_main=False)

    def setUp(self):
        cache.clear()
        EndpointConfig.objects.filter(slug="mains").update(enabled=True, rate_limit_per_minute=0)
        self.key = ApiKey(name="t")
        self.raw = self.key.regenerate()
        self.key.save()
        self.key.allowed_endpoints.set(EndpointConfig.objects.filter(slug="mains"))

    def get(self, slug, **params):
        r = self.client.get(
            reverse("dataexport:endpoint", args=[slug]), params,
            HTTP_AUTHORIZATION=f"Bearer {self.raw}",
        )
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    # ---- nested `mains`

    def test_mains_shape_and_order(self):
        data = self.get("mains")
        self.assertEqual([m["id"] for m in data["results"]], [100, 200, 300])  # by main char id
        two = data["results"][1]
        self.assertEqual(set(two), {"id", "main", "alts", "character_count"})
        self.assertEqual(
            set(two["main"]),
            {"character_id", "character_name", "corporation_id", "corporation_name",
             "alliance_id", "alliance_name"},
        )
        self.assertEqual(two["main"]["character_name"], "Main Two")
        self.assertEqual([a["character_id"] for a in two["alts"]], [201, 202])
        self.assertEqual(two["character_count"], 3)
        self.assertIsNone(two["alts"][0]["alliance_id"])  # alt with no alliance
        self.assertEqual(data["results"][0]["alts"], [])
        self.assertEqual(data["results"][0]["character_count"], 1)

    def test_main_not_repeated_in_alts_and_account_without_main_omitted(self):
        data = self.get("mains")
        for entry in data["results"]:
            self.assertNotIn(entry["main"]["character_id"], [a["character_id"] for a in entry["alts"]])
        all_ids = {entry["main"]["character_id"] for entry in data["results"]}
        self.assertNotIn(400, all_ids)

    def test_no_user_data_leaks(self):
        text = self.client.get(
            reverse("dataexport:endpoint", args=["mains"]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw}",
        ).content.decode()
        for forbidden in ("secret.example", "username", "u1", "owner_hash", "user_id", "email"):
            self.assertNotIn(forbidden, text)

    def test_mains_paging_by_main_character_id(self):
        first = self.get("mains", limit=2)
        self.assertEqual([m["id"] for m in first["results"]], [100, 200])
        self.assertEqual(first["next_after_id"], 200)
        second = self.get("mains", limit=2, after_id=200)
        self.assertEqual([m["id"] for m in second["results"]], [300])
        self.assertIsNone(second["next_after_id"])

    def test_mains_alliance_restriction_uses_the_mains_alliance(self):
        EndpointConfig.objects.filter(slug="mains").update(restrict_alliance_ids="10")
        data = self.get("mains")
        self.assertEqual([m["id"] for m in data["results"]], [100, 200])  # not 300 (alliance 20)
        # Alts keep their own alliance (or none); only the main decides inclusion
        self.assertIsNone(data["results"][1]["alts"][0]["alliance_id"])
        EndpointConfig.objects.filter(slug="mains").update(restrict_alliance_ids="20")
        data = self.get("mains")
        self.assertEqual([m["id"] for m in data["results"]], [300])
        self.assertEqual(data["results"][0]["alts"][0]["alliance_id"], 10)

    def test_page_size_defaults_and_caps(self):
        d = registry.get_definition("mains")
        self.assertEqual((d.default_limit, d.max_limit), (20000, 20000))
        self.assertEqual(self.get("mains", limit=10**6)["count"], 3)

    def test_mains_query_count_constant(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.get("mains")
            return len(ctx)

        base = queries()
        for i in range(8):
            account(f"x{i}", char(1000 + i * 2, f"M{i}"), [char(1001 + i * 2, f"A{i}")])
        self.assertEqual(queries(), base)

    def test_mains_admin_sample_live_with_lists_cut(self):
        account("big", char(50, "Big Main"), [char(60 + i, f"Alt {i}") for i in range(5)])
        s = docs.sample_response(registry.get_definition("mains"), EndpointConfig.objects.get(slug="mains"))
        self.assertTrue(s["live"])
        self.assertIn("lists cut", s["label"])
        body = json.loads(s["text"])
        self.assertEqual(body["results"][0]["id"], 50)
        self.assertLessEqual(len(body["results"][0]["alts"]), docs.SAMPLE_LIST_ITEMS)
        self.assertEqual(body["results"][0]["character_count"], 6)
