from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('sessions_app', '0012_sessiongroup_coininsertrequest_group_pass_devices_and_more'),
    ]

    operations = [
        # Add plan FK to SessionGroup
        migrations.AddField(
            model_name='sessiongroup',
            name='plan',
            field=models.ForeignKey(
                blank=True,
                help_text='The plan each redeemer will receive a full independent session for',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='session_groups',
                to='sessions_app.plan',
            ),
        ),
        # Add code_expires_at
        migrations.AddField(
            model_name='sessiongroup',
            name='code_expires_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='When this code can no longer be redeemed (separate from session expiry)',
            ),
        ),
        # Remove the old shared time_out (it's now per-session via duration_minutes_purchased)
        migrations.RemoveField(
            model_name='sessiongroup',
            name='time_out',
        ),
        # Update status choices to include 'exhausted'
        migrations.AlterField(
            model_name='sessiongroup',
            name='status',
            field=models.CharField(
                choices=[('active', 'Active'), ('expired', 'Expired'), ('exhausted', 'Exhausted')],
                default='active',
                max_length=10,
            ),
        ),
    ]
