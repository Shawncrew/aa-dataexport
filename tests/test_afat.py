"""AFAT handlers against a stand-in schema with the same fields and relations."""
import json
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from afat.models import Fat, FatLink
from allianceauth.eveonline.models import EveCharacter
from dataexport import docs, registry
from dataexport.models import ApiKey, EndpointConfig


class AfatEndpointsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        registry.sync_endpoint_configs()
        now = timezone.now()
        cls.fc = EveCharacter.objects.create(character_id=1, character_name="FC")
        cls.pilots = [
            EveCharacter.objects.create(character_id=100 + i, character_name=f"Pilot {i}")
            for i in range(5)
        ]
        # A (old): pilots 0,1 in alliance 10; pilot 2 in alliance 20
        cls.a = FatLink.objects.create(
            fleet="A", hash="hash-a", character=cls.fc, fleet_type="Strat",
            doctrine="Muninn", is_esilink=True, created=now - timedelta(days=30),
        )
        # B (recent): pilot 3 in alliance 10, no FC character
        cls.b = FatLink.objects.create(
            fleet="B", hash="hash-b", character=None, created=now - timedelta(days=1)
        )
        # C: only alliance 20
        cls.c = FatLink.objects.create(fleet="C", hash="hash-c", created=now - timedelta(days=1))
        # D: nobody attended
        cls.d = FatLink.objects.create(fleet="D", hash="hash-d", created=now)

        def fat(link, pilot, alliance, ship):
            return Fat.objects.create(
                character=pilot, fatlink=link, corporation_eve_id=5,
                alliance_eve_id=alliance, system="Jita", shiptype=ship,
            )

        fat(cls.a, cls.pilots[0], 10, "Muninn")
        fat(cls.a, cls.pilots[1], 10, "Scimitar")
        fat(cls.a, cls.pilots[2], 20, "Zealot")
        fat(cls.b, cls.pilots[3], 10, "Nergal")
        fat(cls.c, cls.pilots[4], 20, "Hurricane")

    def setUp(self):
        cache.clear()
        EndpointConfig.objects.filter(slug="afat-fleets").update(
            enabled=True, rate_limit_per_minute=0
        )
        self.key = ApiKey(name="t")
        self.raw = self.key.regenerate()
        self.key.save()
        self.key.allowed_endpoints.set(EndpointConfig.objects.filter(slug="afat-fleets"))

    def get(self, slug, **params):
        r = self.client.get(
            reverse("dataexport:endpoint", args=[slug]), params,
            HTTP_AUTHORIZATION=f"Bearer {self.raw}",
        )
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_nested_shape(self):
        by_name = {f["fleet_name"]: f for f in self.get("afat-fleets")["results"]}
        self.assertEqual(set(by_name), {"A", "B", "C"})  # D has no members: excluded
        a = by_name["A"]
        self.assertEqual(
            set(a),
            {"id", "hash", "fleet_name", "fleet_type", "doctrine", "created",
             "is_esi_link", "fc", "member_count", "members"},
        )
        self.assertEqual(a["fc"], {"character_id": 1, "character_name": "FC"})
        self.assertEqual(
            (a["hash"], a["fleet_type"], a["doctrine"], a["is_esi_link"]),
            ("hash-a", "Strat", "Muninn", True),
        )
        self.assertEqual(a["member_count"], 3)
        self.assertEqual(
            [m["character_name"] for m in a["members"]], ["Pilot 0", "Pilot 1", "Pilot 2"]
        )
        self.assertEqual(
            set(a["members"][0]),
            {"fat_id", "character_id", "character_name", "corporation_id",
             "alliance_id", "system", "ship_type"},
        )
        self.assertEqual(a["members"][1]["ship_type"], "Scimitar")
        self.assertIsNone(by_name["B"]["fc"])

    def test_no_internal_fields_leak(self):
        text = self.client.get(
            reverse("dataexport:endpoint", args=["afat-fleets"]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw}",
        ).content.decode()
        for forbidden in ("creator", "email", "esi_fleet_id", "discord"):
            self.assertNotIn(forbidden, text)

    def test_limit_and_after_id_count_fleets(self):
        first = self.get("afat-fleets", limit=2)
        self.assertEqual([f["fleet_name"] for f in first["results"]], ["A", "B"])
        self.assertEqual(first["next_after_id"], self.b.pk)
        second = self.get("afat-fleets", limit=2, after_id=first["next_after_id"])
        self.assertEqual([f["fleet_name"] for f in second["results"]], ["C"])
        self.assertIsNone(second["next_after_id"])

    def test_since_filters_by_fleet_created(self):
        since = (timezone.now() - timedelta(days=7)).isoformat()
        data = self.get("afat-fleets", since=since)
        self.assertEqual([f["fleet_name"] for f in data["results"]], ["B", "C"])

    def test_late_joiner_visible_on_repull(self):
        def counts():
            res = self.get("afat-fleets", since="2000-01-01T00:00:00Z")["results"]
            return {f["fleet_name"]: f["member_count"] for f in res}

        before = counts()
        late = EveCharacter.objects.create(character_id=999, character_name="Late")
        Fat.objects.create(
            character=late, fatlink=self.b, alliance_eve_id=10, shiptype="Sabre", system="X"
        )
        self.assertEqual((before["B"], counts()["B"]), (1, 2))

    def test_alliance_restriction_filters_members_and_fleets(self):
        EndpointConfig.objects.filter(slug="afat-fleets").update(restrict_alliance_ids="10")
        by_name = {f["fleet_name"]: f for f in self.get("afat-fleets")["results"]}
        self.assertEqual(set(by_name), {"A", "B"})  # C only had alliance 20
        self.assertEqual(
            [m["character_name"] for m in by_name["A"]["members"]], ["Pilot 0", "Pilot 1"]
        )
        self.assertEqual(by_name["A"]["member_count"], 2)
        # paging stays correct: an excluded fleet never produces a short page
        page = self.get("afat-fleets", limit=2)
        self.assertEqual(len(page["results"]), 2)
        self.assertEqual(page["next_after_id"], self.b.pk)
        self.assertEqual(self.get("afat-fleets", limit=2, after_id=self.b.pk)["results"], [])

    def test_endpoint_page_size_limits(self):
        d = registry.get_definition("afat-fleets")
        self.assertEqual((d.default_limit, d.max_limit), (1000, 1000))
        self.assertEqual(len(self.get("afat-fleets", limit=1)["results"]), 1)
        self.assertEqual(self.get("afat-fleets", limit=10**6)["count"], 3)

    def test_query_count_does_not_grow_with_fleets(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.get("afat-fleets")
            return len(ctx)

        base = queries()
        for i in range(10):
            link = FatLink.objects.create(fleet=f"X{i}", hash=f"x{i}")
            p = EveCharacter.objects.create(character_id=5000 + i, character_name=f"P{i}")
            Fat.objects.create(character=p, fatlink=link, alliance_eve_id=10)
        self.assertEqual(queries(), base)

    def test_nested_admin_sample_is_live_and_lists_cut(self):
        Fat.objects.create(  # a 4th member on fleet A, so its list exceeds the 3-item cut
            character=self.pilots[3], fatlink=self.a, alliance_eve_id=10, shiptype="X", system="Y"
        )
        cfg = EndpointConfig.objects.get(slug="afat-fleets")
        s = docs.sample_response(registry.get_definition("afat-fleets"), cfg)
        self.assertTrue(s["live"])
        self.assertIn("lists cut", s["label"])
        body = json.loads(s["text"])
        self.assertLessEqual(
            max(len(f["members"]) for f in body["results"]), docs.SAMPLE_LIST_ITEMS
        )
        self.assertEqual(body["results"][0]["member_count"], 4)  # true count survives the cut
