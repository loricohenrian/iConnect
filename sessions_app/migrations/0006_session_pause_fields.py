from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0005_coininsertrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="paused_at",
            field=models.DateTimeField(
                blank=True, help_text="When session was paused", null=True
            ),
        ),
        migrations.AddField(
            model_name="session",
            name="total_paused_seconds",
            field=models.FloatField(
                default=0, help_text="Total seconds spent paused"
            ),
        ),
    ]
