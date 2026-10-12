from django.db import migrations

REMOVED = ["afat", "characters"]


def remove_flat_endpoints(apps, schema_editor):
    # The flat endpoints were replaced by the nested "afat-fleets" and "mains". Deleting
    # their config rows also drops them from any key's allowed endpoints (M2M rows go
    # with them), so a key never holds a grant to an endpoint that no longer exists.
    EndpointConfig = apps.get_model("dataexport", "EndpointConfig")
    EndpointConfig.objects.filter(slug__in=REMOVED).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dataexport", "0002_endpoint_rate_limit"),
    ]

    operations = [
        migrations.RunPython(remove_flat_endpoints, migrations.RunPython.noop),
    ]
