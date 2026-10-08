from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("sessions_app", "0018_alter_coinevent_mac_address_and_more")]

    operations = [
        migrations.CreateModel(
            name="SessionPowerState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("boot_id", models.CharField(default="", max_length=64)),
                ("checkpoint_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.AddField(model_name="session", name="power_paused", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="session", name="power_credited_seconds", field=models.FloatField(default=0)),
        migrations.AddField(model_name="session", name="power_checkpoint_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="session", name="power_remaining_seconds", field=models.FloatField(blank=True, null=True)),
    ]
