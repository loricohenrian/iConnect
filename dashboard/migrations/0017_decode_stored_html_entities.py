import html

from django.db import migrations


TEXT_FIELDS = (
    ('dashboard', 'Announcement', ('message',)),
    ('dashboard', 'ProjectCost', ('description',)),
    ('dashboard', 'OperatingExpense', ('name',)),
    (
        'dashboard',
        'IssueReport',
        ('contact_info', 'message', 'admin_reply', 'admin_notes'),
    ),
    ('sessions_app', 'Plan', ('name',)),
    ('sessions_app', 'Session', ('device_name',)),
    ('sessions_app', 'WhitelistedDevice', ('device_name', 'added_by')),
    ('sessions_app', 'SuspiciousDevice', ('reason', 'evidence', 'resolved_by')),
    ('sessions_app', 'SpinPrize', ('name',)),
)


def _decode_entities(value):
    if not isinstance(value, str) or '&' not in value:
        return value

    decoded = value
    for _ in range(5):
        next_value = html.unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def decode_stored_html_entities(apps, schema_editor):
    """Repair text that was HTML-escaped before being stored."""
    for app_label, model_name, field_names in TEXT_FIELDS:
        model = apps.get_model(app_label, model_name)
        pending = []

        for item in model.objects.all().only('pk', *field_names).iterator(chunk_size=500):
            changed = False
            for field_name in field_names:
                original = getattr(item, field_name)
                decoded = _decode_entities(original)
                if decoded != original:
                    setattr(item, field_name, decoded)
                    changed = True

            if changed:
                pending.append(item)

            if len(pending) >= 500:
                model.objects.bulk_update(pending, field_names, batch_size=500)
                pending = []

        if pending:
            model.objects.bulk_update(pending, field_names, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0016_issuereport_customer_reply'),
        ('sessions_app', '0018_alter_coinevent_mac_address_and_more'),
    ]

    operations = [
        migrations.RunPython(decode_stored_html_entities, migrations.RunPython.noop),
    ]
