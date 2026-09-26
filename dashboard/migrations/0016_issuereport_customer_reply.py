from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0015_systemsettings_max_session_pause_cap_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='issuereport',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('answered', 'Answered'),
                    ('resolved', 'Resolved'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='issuereport',
            name='admin_reply',
            field=models.TextField(
                blank=True,
                help_text='Customer-facing reply shown in the captive portal',
            ),
        ),
        migrations.AddField(
            model_name='issuereport',
            name='replied_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='issuereport',
            name='user_viewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
