from django.db import migrations, models
from django.db.models import Q


def fix_existing_session_baselines(apps, schema_editor):
    Session = apps.get_model('sessions_app', 'Session')
    for s in Session.objects.filter(Q(initial_bandwidth_mb=0) | Q(initial_bandwidth_mb__isnull=True)).order_by('id'):
        if not s.mac_address:
            continue
        prev = Session.objects.filter(
            mac_address=s.mac_address,
            id__lt=s.id
        ).order_by('-id').first()
        if prev and prev.bandwidth_used_mb and s.bandwidth_used_mb >= prev.bandwidth_used_mb > 0:
            s.initial_bandwidth_mb = prev.bandwidth_used_mb
            s.bandwidth_used_mb = round(max(0.0, s.bandwidth_used_mb - prev.bandwidth_used_mb), 1)
            s.save(update_fields=['initial_bandwidth_mb', 'bandwidth_used_mb'])


class Migration(migrations.Migration):

    dependencies = [
        ('sessions_app', '0015_sessiongroup_redeemed_count_alter_session_plan'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='initial_bandwidth_mb',
            field=models.FloatField(blank=True, default=None, help_text='Baseline hardware byte counter (in MB) at session start', null=True),
        ),
        migrations.RunPython(fix_existing_session_baselines, migrations.RunPython.noop),
    ]

