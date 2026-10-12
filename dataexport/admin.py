from django.contrib import admin, messages
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from . import docs
from .models import ApiKey, EndpointConfig
from .registry import all_definitions, get_definition, sync_endpoint_configs


def _show_key(request, key: ApiKey, raw: str):
    messages.warning(
        request,
        format_html(
            "API key for <b>{}</b> - copy it now, it cannot be shown again:<br>"
            "<code style='user-select:all'>{}</code><br>"
            "Test it (lists the endpoints this key may call):<br>"
            "<code style='user-select:all'>{}</code>",
            key.name,
            raw,
            docs.curl_for(request, key=raw),
        ),
    )


@admin.register(EndpointConfig)
class EndpointConfigAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "enabled",
        "restrict_alliance_ids",
        "rate_limit_per_minute",
        "path",
        "key_count",
        "sample",
    )
    list_editable = ("enabled", "restrict_alliance_ids", "rate_limit_per_minute")
    fields = (
        "slug",
        "title",
        "description",
        "enabled",
        "restrict_alliance_ids",
        "rate_limit_per_minute",
    )
    readonly_fields = ("slug", "title", "description")
    change_list_template = "admin/dataexport/endpointconfig/change_list.html"
    change_form_template = "admin/dataexport/endpointconfig/change_form.html"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        sync_endpoint_configs()
        extra_context = extra_context or {}
        configs = {c.slug: c for c in EndpointConfig.objects.all()}
        extra_context["guide"] = {
            "index_curl": docs.curl_for(request),
            "endpoints": [
                {
                    "title": d.title,
                    "curl": docs.curl_for(request, d.slug, d.example_query),
                    "sample": docs.sample_response(d, configs[d.slug]),
                }
                for d in all_definitions()
                if d.slug in configs
            ],
            "paging_note": docs.PAGING_NOTE,
        }
        return super().changelist_view(request, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        d = get_definition(obj.slug) if obj else None
        if d is not None:
            extra_context["example"] = {
                "curl": docs.curl_for(request, d.slug, d.example_query),
                "sample": docs.sample_response(d, obj),
            }
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.display(description="Endpoint")
    def title(self, obj):
        d = get_definition(obj.slug)
        return d.title if d else f"{obj.slug} (not provided by this version)"

    @admin.display(description="Description")
    def description(self, obj):
        d = get_definition(obj.slug)
        return d.description if d else ""

    @admin.display(description="Path")
    def path(self, obj):
        try:
            return reverse("dataexport:endpoint", args=[obj.slug])
        except NoReverseMatch:
            return ""

    @admin.display(description="Sample data")
    def sample(self, obj):
        d = get_definition(obj.slug)
        if d is None:
            return ""
        sample = docs.sample_response(d, obj)
        return format_html(
            "<details><summary style='cursor:pointer'>Show sample response</summary>"
            "<div style='font-size:11px;margin-top:4px'>{}</div>"
            "<pre style='max-width:520px;max-height:22em;overflow:auto;margin:6px 0 0;"
            "padding:8px;background:var(--darkened-bg);white-space:pre'>{}</pre></details>",
            sample["label"],
            sample["text"],
        )

    @admin.display(description="Keys")
    def key_count(self, obj):
        return obj.api_keys.filter(is_active=True).count()


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "key_prefix",
        "is_active",
        "expires_at",
        "endpoints",
        "last_used_at",
        "last_used_ip",
        "request_count",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "key_prefix", "notes")
    filter_horizontal = ("allowed_endpoints",)
    actions = ["regenerate_keys", "revoke_keys"]

    def get_fields(self, request, obj=None):
        fields = [
            "name",
            "is_active",
            "expires_at",
            "allowed_endpoints",
            "allowed_ips",
            "notes",
        ]
        if obj:
            fields += [
                "key_prefix",
                "created",
                "created_by",
                "last_used_at",
                "last_used_ip",
                "request_count",
            ]
        return fields

    def get_readonly_fields(self, request, obj=None):
        return (
            "key_prefix",
            "created",
            "created_by",
            "last_used_at",
            "last_used_ip",
            "request_count",
        )

    def get_form(self, request, obj=None, **kwargs):
        sync_endpoint_configs()  # so every endpoint is selectable before first visit
        return super().get_form(request, obj, **kwargs)

    @admin.display(description="Endpoints")
    def endpoints(self, obj):
        return ", ".join(e.slug for e in obj.allowed_endpoints.all()) or "-"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("allowed_endpoints")

    def save_model(self, request, obj, form, change):
        raw = None
        if not change:
            obj.created_by = request.user
            raw = obj.regenerate()
        super().save_model(request, obj, form, change)
        if raw:
            _show_key(request, obj, raw)

    @admin.action(description="Regenerate key (the old key stops working immediately)")
    def regenerate_keys(self, request, queryset):
        for key in queryset:
            raw = key.regenerate()
            key.save(update_fields=["key_hash", "key_prefix"])
            _show_key(request, key, raw)

    @admin.action(description="Revoke (deactivate) selected keys")
    def revoke_keys(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f"Revoked {n} key(s).")
