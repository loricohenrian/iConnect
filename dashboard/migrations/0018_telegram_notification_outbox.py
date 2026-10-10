from django.db import migrations, models
import django.utils.timezone


def disable_midnight_telegram(apps, schema_editor):
    apps.get_model('dashboard', 'SystemSettings').objects.using(schema_editor.connection.alias).update(
        telegram_notify_daily_summary=False,
    )


class Migration(migrations.Migration):
    dependencies = [('dashboard', '0017_decode_stored_html_entities')]
    operations = [
        migrations.AddField(
            model_name='systemsettings', name='telegram_notify_security',
            field=models.BooleanField(default=True, help_text='Send alerts for new or reopened security incidents'),
        ),
        migrations.AlterField(
            model_name='systemsettings', name='telegram_notify_daily_summary',
            field=models.BooleanField(default=False, help_text='Send daily midnight sales summary'),
        ),
        migrations.CreateModel(
            name='TelegramNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=150, unique=True)),
                ('kind', models.CharField(max_length=20, choices=[('ticket', 'Support Ticket'), ('security', 'Security Alert')])),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('next_attempt_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('lease_until', models.DateTimeField(blank=True, null=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ['created_at', 'pk']},
        ),
        migrations.RunPython(disable_midnight_telegram, migrations.RunPython.noop),
    ]
