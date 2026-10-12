from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from dataexport import registry
from dataexport.models import ApiKey, EndpointConfig
from dataexport.registry import ParamError, register


@register("demo", "Demo", "Test endpoint")
def _demo(request, config, after_id, limit):
    if request.GET.get("boom"):
        raise ParamError("boom")
    return [{"id": i, "alliances": config.alliance_ids} for i in range(after_id + 1, after_id + 1 + limit)]


@register("needs-missing-app", "Missing", "Requires an app that isn't installed", required_app="nope")
def _missing(request, config, after_id, limit):
    return []


def make_key(name="k", endpoints=("demo",), **kwargs):
    key = ApiKey(name=name, **kwargs)
    raw = key.regenerate()
    key.save()
    for slug in endpoints:
        key.allowed_endpoints.add(EndpointConfig.objects.get(slug=slug))
    return key, raw


class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        registry.sync_endpoint_configs()
        EndpointConfig.objects.filter(slug="demo").update(enabled=True)
        self.url = reverse("dataexport:endpoint", args=["demo"])

    def get(self, raw=None, url=None, header="Authorization", **params):
        headers = {}
        if raw and header == "Authorization":
            headers["HTTP_AUTHORIZATION"] = f"Bearer {raw}"
        elif raw:
            headers["HTTP_X_API_KEY"] = raw
        return self.client.get(url or self.url, params, **headers)

    def test_endpoints_default_disabled_after_sync(self):
        self.assertFalse(EndpointConfig.objects.get(slug="needs-missing-app").enabled)

    def test_no_key_and_bad_key_are_401(self):
        self.assertEqual(self.get().status_code, 401)
        self.assertEqual(self.get("aadx_wrong").status_code, 401)

    def test_valid_key_bearer_and_x_api_key(self):
        _, raw = make_key()
        self.assertEqual(self.get(raw).status_code, 200)
        self.assertEqual(self.get(raw, header="X-API-Key").status_code, 200)

    def test_raw_key_not_stored(self):
        key, raw = make_key()
        self.assertNotIn(raw, (key.key_hash, key.key_prefix))
        self.assertNotEqual(key.key_hash, raw)
        self.assertTrue(raw.startswith(key.key_prefix))

    def test_inactive_and_expired_are_401(self):
        _, raw = make_key("a", is_active=False)
        self.assertEqual(self.get(raw).status_code, 401)
        _, raw = make_key("b", expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.get(raw).status_code, 401)

    def test_key_without_endpoint_is_403(self):
        _, raw = make_key(endpoints=())
        self.assertEqual(self.get(raw).status_code, 403)

    def test_disabled_endpoint_is_404_even_if_granted(self):
        _, raw = make_key()
        EndpointConfig.objects.filter(slug="demo").update(enabled=False)
        self.assertEqual(self.get(raw).status_code, 404)

    def test_unknown_endpoint_404(self):
        _, raw = make_key()
        url = reverse("dataexport:endpoint", args=["nope"])
        self.assertEqual(self.get(raw, url=url).status_code, 404)
        self.assertEqual(self.get(url=url).status_code, 401)

    def test_missing_required_app_503(self):
        EndpointConfig.objects.filter(slug="needs-missing-app").update(enabled=True)
        _, raw = make_key(endpoints=("needs-missing-app",))
        url = reverse("dataexport:endpoint", args=["needs-missing-app"])
        self.assertEqual(self.get(raw, url=url).status_code, 503)

    def test_ip_allowlist(self):
        _, raw = make_key(allowed_ips="10.1.0.0/16\n192.0.2.7")
        ok = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {raw}", REMOTE_ADDR="10.1.2.3")
        bad = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {raw}", REMOTE_ADDR="10.2.2.3")
        self.assertEqual((ok.status_code, bad.status_code), (200, 401))

    @override_settings()
    def test_client_ip_header(self):
        from dataexport import app_settings

        _, raw = make_key(allowed_ips="203.0.113.5")
        old = app_settings.DATAEXPORT_CLIENT_IP_HEADER
        app_settings.DATAEXPORT_CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"
        try:
            r = self.client.get(
                self.url,
                HTTP_AUTHORIZATION=f"Bearer {raw}",
                REMOTE_ADDR="172.18.0.2",
                HTTP_CF_CONNECTING_IP="203.0.113.5",
            )
        finally:
            app_settings.DATAEXPORT_CLIENT_IP_HEADER = old
        self.assertEqual(r.status_code, 200)

    def test_pagination_and_limits(self):
        _, raw = make_key()
        data = self.get(raw, limit=3).json()
        self.assertEqual((data["count"], data["next_after_id"]), (3, 3))
        data = self.get(raw, limit=3, after_id=3).json()
        self.assertEqual([r["id"] for r in data["results"]], [4, 5, 6])
        self.assertEqual(self.get(raw, limit="x").status_code, 400)
        self.assertEqual(self.get(raw, limit=0).status_code, 400)
        self.assertEqual(self.get(raw, boom=1).status_code, 400)
        from dataexport import app_settings

        self.assertEqual(self.get(raw, limit=10**6).json()["count"], app_settings.DATAEXPORT_MAX_PAGE_SIZE)

    def test_alliance_restriction_reaches_handler(self):
        _, raw = make_key()
        EndpointConfig.objects.filter(slug="demo").update(restrict_alliance_ids="1,2")
        data = self.get(raw, limit=2).json()
        self.assertEqual(data["results"][0]["alliances"], [1, 2])

    def test_usage_tracking(self):
        key, raw = make_key()
        self.get(raw)
        self.get(raw)
        key.refresh_from_db()
        self.assertEqual(key.request_count, 2)
        self.assertIsNotNone(key.last_used_at)

    def test_rate_limit(self):
        from dataexport import app_settings

        _, raw = make_key()
        old = app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE
        app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE = 2
        try:
            codes = [self.get(raw).status_code for _ in range(3)]
        finally:
            app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE = old
        self.assertEqual(codes, [200, 200, 429])

    def test_index_lists_only_granted_enabled(self):
        EndpointConfig.objects.filter(slug="needs-missing-app").update(enabled=True)
        _, raw = make_key(endpoints=("demo", "needs-missing-app"))
        EndpointConfig.objects.filter(slug="needs-missing-app").update(enabled=False)
        data = self.get(raw, url=reverse("dataexport:index")).json()
        self.assertEqual([e["slug"] for e in data["endpoints"]], ["demo"])

    def test_post_not_allowed(self):
        _, raw = make_key()
        r = self.client.post(self.url, HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(r.status_code, 405)

    def test_no_store_header(self):
        _, raw = make_key()
        self.assertEqual(self.get(raw)["Cache-Control"], "no-store")


class AdminTests(TestCase):
    def setUp(self):
        registry.sync_endpoint_configs()
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(self.admin)

    def test_create_key_shows_raw_key_once_and_stores_hash(self):
        demo = EndpointConfig.objects.get(slug="demo")
        r = self.client.post(
            reverse("admin:dataexport_apikey_add"),
            {"name": "other-alliance", "is_active": "on", "allowed_endpoints": [demo.pk],
             "allowed_ips": "", "notes": ""},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        key = ApiKey.objects.get(name="other-alliance")
        self.assertEqual(key.created_by, self.admin)
        self.assertEqual(list(key.allowed_endpoints.all()), [demo])
        shown = [str(m) for m in r.context["messages"]]
        raw = next(m for m in shown if "aadx_" in m).split("<code")[1].split(">")[1].split("<")[0]
        from dataexport import security

        self.assertEqual(security.hash_key(raw), key.key_hash)
        # not shown again on the change page
        r = self.client.get(reverse("admin:dataexport_apikey_change", args=[key.pk]))
        self.assertNotContains(r, raw)

    def test_bad_cidr_rejected(self):
        r = self.client.post(
            reverse("admin:dataexport_apikey_add"),
            {"name": "x", "is_active": "on", "allowed_ips": "not-an-ip", "notes": ""},
        )
        self.assertContains(r, "Invalid IP/CIDR")
        self.assertFalse(ApiKey.objects.exists())

    def test_regenerate_invalidates_old_key(self):
        key, old_raw = make_key()
        old_hash = key.key_hash
        self.client.post(
            reverse("admin:dataexport_apikey_changelist"),
            {"action": "regenerate_keys", "_selected_action": [key.pk]},
        )
        key.refresh_from_db()
        self.assertNotEqual(key.key_hash, old_hash)

    def test_revoke_action(self):
        key, _ = make_key()
        self.client.post(
            reverse("admin:dataexport_apikey_changelist"),
            {"action": "revoke_keys", "_selected_action": [key.pk]},
        )
        key.refresh_from_db()
        self.assertFalse(key.is_active)

    def test_endpoint_toggle_and_no_add_delete(self):
        r = self.client.get(reverse("admin:dataexport_endpointconfig_changelist"))
        self.assertContains(r, "Demo")
        self.assertEqual(self.client.get(reverse("admin:dataexport_endpointconfig_add")).status_code, 403)
        cfg = EndpointConfig.objects.get(slug="demo")
        r = self.client.post(
            reverse("admin:dataexport_endpointconfig_change", args=[cfg.pk]),
            {"enabled": "on", "restrict_alliance_ids": "abc", "rate_limit_per_minute": "30"},
        )
        self.assertContains(r, "comma separated integers")
        self.client.post(
            reverse("admin:dataexport_endpointconfig_change", args=[cfg.pk]),
            {"enabled": "on", "restrict_alliance_ids": "123, 456", "rate_limit_per_minute": "30"},
        )
        cfg.refresh_from_db()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.alliance_ids, [123, 456])


class AdminDocsTests(TestCase):
    def setUp(self):
        registry.sync_endpoint_configs()
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "pw"))

    def test_changelist_shows_curl_guide(self):
        r = self.client.get(reverse("admin:dataexport_endpointconfig_changelist"))
        self.assertContains(r, "How to call the API")
        self.assertContains(r, 'Authorization: Bearer aadx_YOUR_KEY')
        self.assertContains(r, "/dataexport/api/v1/")

    def test_builtin_endpoint_pages_show_example_response(self):
        for slug, needle in (("mains", "Example Main"), ("afat-fleets", "Example Pilot")):
            cfg = EndpointConfig.objects.get(slug=slug)
            r = self.client.get(reverse("admin:dataexport_endpointconfig_change", args=[cfg.pk]))
            self.assertContains(r, needle)
            self.assertContains(r, f"/dataexport/api/v1/{slug}/?")

    def test_new_key_banner_has_working_curl(self):
        r = self.client.post(
            reverse("admin:dataexport_apikey_add"),
            {"name": "n", "is_active": "on", "allowed_ips": "", "notes": ""},
            follow=True,
        )
        banner = " ".join(str(m) for m in r.context["messages"])
        self.assertIn("curl -s -H", banner)
        self.assertIn("Authorization: Bearer aadx_", banner)

    def test_example_rows_match_handler_field_names(self):
        # Samples must not drift from what the handlers emit: compare key sets via source.
        import inspect

        from dataexport import endpoints

        for slug, fn in (("afat-fleets", endpoints.afat_fleets), ("mains", endpoints.mains)):
            src = inspect.getsource(fn)
            for key in registry.get_definition(slug).example_rows[0]:
                self.assertIn(f'"{key}"', src, f"{slug}: sample field {key} not emitted by handler")


class DocsBaseUrlTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory

        self.rf = RequestFactory()

    def test_forwarded_proto_gives_https(self):
        from dataexport import docs

        req = self.rf.get("/", HTTP_HOST="auth.example.org", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(docs.base_url(req), "https://auth.example.org/dataexport/api/v1/")

    def test_configured_base_url_wins(self):
        from dataexport import app_settings, docs

        old = app_settings.DATAEXPORT_PUBLIC_BASE_URL
        app_settings.DATAEXPORT_PUBLIC_BASE_URL = "https://pub.example.org"
        try:
            req = self.rf.get("/", HTTP_HOST="internal")
            self.assertEqual(docs.base_url(req), "https://pub.example.org/dataexport/api/v1/")
        finally:
            app_settings.DATAEXPORT_PUBLIC_BASE_URL = old

    def test_plain_request_uses_its_scheme(self):
        from dataexport import docs

        req = self.rf.get("/", HTTP_HOST="auth.example.org")
        self.assertTrue(docs.base_url(req).startswith("http://auth.example.org/"))


class AdminLayoutTests(TestCase):
    def test_sample_dropdown_per_row_and_guide_after_table(self):
        registry.sync_endpoint_configs()
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "pw"))
        r = self.client.get(reverse("admin:dataexport_endpointconfig_changelist"))
        html = r.content.decode()
        self.assertGreaterEqual(html.count("Show sample response"), 2)
        self.assertIn("Example Main", html)
        self.assertGreater(html.index("How to call the API"), html.index("Show sample response"))


class GuideResponseDropdownTests(TestCase):
    def test_guide_has_collapsed_example_response_per_endpoint(self):
        registry.sync_endpoint_configs()
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "pw"))
        html = self.client.get(reverse("admin:dataexport_endpointconfig_changelist")).content.decode()
        guide = html[html.index("How to call the API"):]
        self.assertGreaterEqual(guide.count("Show example response"), 2)
        self.assertIn("<details", guide)
        self.assertNotIn("<details open", guide)
        self.assertIn("character_count", guide)  # the nested mains sample


class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        registry.sync_endpoint_configs()
        EndpointConfig.objects.filter(slug="demo").update(enabled=True)
        self.url = reverse("dataexport:endpoint", args=["demo"])

    def call(self, raw, url=None):
        return self.client.get(url or self.url, HTTP_AUTHORIZATION=f"Bearer {raw}")

    def test_default_endpoint_limit_is_30(self):
        self.assertEqual(EndpointConfig.objects.get(slug="demo").rate_limit_per_minute, 30)

    def test_endpoint_limit_429_with_retry_after_and_headers(self):
        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=2)
        _, raw = make_key()
        r1, r2, r3 = self.call(raw), self.call(raw), self.call(raw)
        self.assertEqual([r1.status_code, r2.status_code, r3.status_code], [200, 200, 429])
        self.assertEqual(r1["X-RateLimit-Limit"], "2")
        self.assertEqual(r1["X-RateLimit-Remaining"], "1")
        self.assertEqual(r2["X-RateLimit-Remaining"], "0")
        self.assertTrue(1 <= int(r3["Retry-After"]) <= 60)
        self.assertEqual(r3["X-RateLimit-Remaining"], "0")

    def test_limit_is_per_key(self):
        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=1)
        _, a = make_key("a")
        _, b = make_key("b")
        self.assertEqual(self.call(a).status_code, 200)
        self.assertEqual(self.call(a).status_code, 429)
        self.assertEqual(self.call(b).status_code, 200)

    def test_limit_is_per_endpoint(self):
        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=1)
        EndpointConfig.objects.filter(slug="needs-missing-app").update(enabled=True)
        _, raw = make_key(endpoints=("demo", "needs-missing-app"))
        other = reverse("dataexport:endpoint", args=["needs-missing-app"])
        self.assertEqual(self.call(raw).status_code, 200)
        self.assertEqual(self.call(raw).status_code, 429)
        self.assertEqual(self.call(raw, other).status_code, 503)  # not throttled by demo's bucket

    def test_zero_means_no_endpoint_limit(self):
        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=0)
        _, raw = make_key()
        codes = {self.call(raw).status_code for _ in range(35)}
        self.assertEqual(codes, {200})
        self.assertNotIn("X-RateLimit-Limit", self.call(raw))

    def test_overall_key_limit_still_caps_endpoint(self):
        from dataexport import app_settings

        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=0)
        _, raw = make_key()
        old = app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE
        app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE = 2
        try:
            codes = [self.call(raw).status_code for _ in range(3)]
            retry = self.call(raw)["Retry-After"]
        finally:
            app_settings.DATAEXPORT_RATE_LIMIT_PER_MINUTE = old
        self.assertEqual(codes, [200, 200, 429])
        self.assertTrue(retry.isdigit())

    def test_index_not_counted_against_endpoint_limit(self):
        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=1)
        _, raw = make_key()
        for _ in range(3):
            self.assertEqual(self.call(raw, reverse("dataexport:index")).status_code, 200)
        self.assertEqual(self.call(raw).status_code, 200)

    def test_denied_requests_do_not_consume_endpoint_budget(self):
        EndpointConfig.objects.filter(slug="demo").update(rate_limit_per_minute=1)
        _, no_access = make_key("noaccess", endpoints=())
        _, ok = make_key("ok")
        for _ in range(3):
            self.assertEqual(self.call(no_access).status_code, 403)
        self.assertEqual(self.call(ok).status_code, 200)

    def test_admin_can_edit_limit_and_guide_mentions_throttle(self):
        admin_user = get_user_model().objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(admin_user)
        cfg = EndpointConfig.objects.get(slug="demo")
        self.client.post(
            reverse("admin:dataexport_endpointconfig_change", args=[cfg.pk]),
            {"enabled": "on", "restrict_alliance_ids": "", "rate_limit_per_minute": "7"},
        )
        cfg.refresh_from_db()
        self.assertEqual(cfg.rate_limit_per_minute, 7)
        html = self.client.get(reverse("admin:dataexport_endpointconfig_changelist")).content.decode()
        self.assertIn("Retry-After", html)


class LiveSampleTests(TestCase):
    def setUp(self):
        registry.sync_endpoint_configs()
        self.demo = registry.get_definition("demo")
        self.cfg = EndpointConfig.objects.get(slug="demo")

    def test_live_sample_is_limited_to_three_rows(self):
        from dataexport import docs

        s = docs.sample_response(self.demo, self.cfg)
        self.assertTrue(s["live"])
        self.assertIn("real data", s["label"])
        import json

        body = json.loads(s["text"])
        self.assertEqual(body["count"], 3)
        self.assertEqual([r["id"] for r in body["results"]], [1, 2, 3])
        self.assertEqual(body["next_after_id"], 3)  # same as the real API at limit=3

    def test_handler_error_falls_back_to_fictional(self):
        from dataexport import docs

        def boom(request, config, after_id, limit):
            raise RuntimeError("db down")

        d = registry.EndpointDefinition("boom", "Boom", "x", boom, example_rows=({"id": 1},))
        s = docs.sample_response(d, EndpointConfig.objects.get(slug="mains"))
        self.assertFalse(s["live"])
        self.assertIn("RuntimeError", s["label"])
        self.assertIn("fictional", s["label"])
        self.assertIn("live data unavailable", s["label"])
        self.assertIn("results", s["text"])

    def test_afat_without_data_falls_back_with_no_rows(self):
        from dataexport import docs

        for slug in ("afat-fleets", "mains"):
            d = registry.get_definition(slug)
            s = docs.sample_response(d, EndpointConfig.objects.get(slug=slug))
            self.assertFalse(s["live"])
            self.assertIn("no rows", s["label"])

    def test_no_rows_falls_back(self):
        from dataexport import docs

        d = registry.get_definition("needs-missing-app")  # handler returns []
        s = docs.sample_response(d, EndpointConfig.objects.get(slug="needs-missing-app"))
        self.assertFalse(s["live"])
        self.assertIn("no rows", s["label"])

    def test_long_values_and_total_size_are_capped(self):
        from dataexport import docs
        from dataexport.registry import EndpointDefinition

        big = EndpointDefinition(
            "big", "Big", "x",
            lambda request, config, after_id, limit: [
                {"id": i, "name": "x" * 500, **{f"f{j}": "y" * 70 for j in range(60)}}
                for i in range(1, limit + 1)
            ],
        )
        s = docs.sample_response(big, self.cfg)
        self.assertTrue(s["live"])
        self.assertNotIn("x" * 100, s["text"])
        self.assertLessEqual(len(s["text"]), docs.MAX_SAMPLE_CHARS + 30)
        self.assertIn("truncated", s["text"])

    def test_alliance_restriction_applies_to_sample(self):
        from dataexport import docs
        import json

        self.cfg.restrict_alliance_ids = "5,6"
        s = docs.sample_response(self.demo, self.cfg)
        self.assertEqual(json.loads(s["text"])["results"][0]["alliances"], [5, 6])

    def test_admin_pages_render_live_sample_without_error(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "pw"))
        EndpointConfig.objects.filter(slug="demo").update(enabled=True)
        r = self.client.get(reverse("admin:dataexport_endpointconfig_changelist"))
        self.assertContains(r, "real data, first 3 rows only")
        r = self.client.get(reverse("admin:dataexport_endpointconfig_change", args=[self.cfg.pk]))
        self.assertContains(r, "real data, first 3 rows only")


class FlatEndpointRemovalTests(TestCase):
    def test_flat_endpoints_are_no_longer_registered(self):
        slugs = {d.slug for d in registry.all_definitions()}
        self.assertTrue({"afat-fleets", "mains"} <= slugs)
        self.assertFalse({"afat", "characters"} & slugs)
        registry.sync_endpoint_configs()
        self.assertFalse(EndpointConfig.objects.filter(slug__in=["afat", "characters"]).exists())

    def test_migration_removes_old_rows_and_key_grants_but_keeps_others(self):
        import importlib

        from django.apps import apps as django_apps

        mig = importlib.import_module("dataexport.migrations.0003_remove_flat_endpoints")
        registry.sync_endpoint_configs()
        old = [EndpointConfig.objects.create(slug="afat"), EndpointConfig.objects.create(slug="characters")]
        keep = EndpointConfig.objects.get(slug="mains")
        key, _ = make_key("legacy", endpoints=("mains",))
        key.allowed_endpoints.add(*old)
        self.assertEqual(key.allowed_endpoints.count(), 3)

        mig.remove_flat_endpoints(django_apps, None)

        self.assertFalse(EndpointConfig.objects.filter(slug__in=["afat", "characters"]).exists())
        self.assertEqual(list(key.allowed_endpoints.all()), [keep])

    def test_old_urls_now_404_for_a_valid_key(self):
        registry.sync_endpoint_configs()
        _, raw = make_key("k", endpoints=("mains",))
        for slug in ("afat", "characters"):
            r = self.client.get(
                reverse("dataexport:endpoint", args=[slug]), HTTP_AUTHORIZATION=f"Bearer {raw}"
            )
            self.assertEqual(r.status_code, 404)
