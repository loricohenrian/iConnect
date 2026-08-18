from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0007_systemsettings_enable_family_pass_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='group_code_expiry_hours',
            field=models.PositiveIntegerField(
                default=24,
                help_text='Hours after purchase before a group code can no longer be redeemed (0 = no expiry)',
            ),
        ),
    ]
