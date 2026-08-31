from django.core.management.base import BaseCommand
from django.db import transaction

class Command(BaseCommand):
    help = "Wipes all user sessions, coin events, device profiles, transactions, and analytics history while preserving plans, admin users, and system settings."

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirm wiping all users and history data.',
        )

    def handle(self, *args, **options):
        if not options.get('confirm'):
            self.stdout.write(
                self.style.WARNING(
                    "WARNING: This will delete all user records, sessions, coin logs, and revenue history.\n"
                    "Run with --confirm to proceed: python manage.py wipe_history --confirm"
                )
            )
            return

        from sessions_app.models import (
            Session, CoinEvent, PurchaseTransaction,
            CoinInsertRequest, DeviceProfile, SessionGroup
        )
        from dashboard.models import (
            DailyRevenueSummary, DailyAnalyticsSnapshot
        )

        with transaction.atomic():
            s_count = Session.objects.all().count()
            Session.objects.all().delete()

            c_count = CoinEvent.objects.all().count()
            CoinEvent.objects.all().delete()

            t_count = PurchaseTransaction.objects.all().count()
            PurchaseTransaction.objects.all().delete()

            r_count = CoinInsertRequest.objects.all().count()
            CoinInsertRequest.objects.all().delete()

            d_count = DeviceProfile.objects.all().count()
            DeviceProfile.objects.all().delete()

            g_count = SessionGroup.objects.all().count()
            SessionGroup.objects.all().delete()

            DailyRevenueSummary.objects.all().delete()
            DailyAnalyticsSnapshot.objects.all().delete()

        # Flush iptables client rules
        try:
            from sessions_app.iptables import flush_all_rules, enforce_firewall_baseline
            flush_all_rules()
            enforce_firewall_baseline()
            self.stdout.write(self.style.SUCCESS("Flushed active firewall rules and restored baseline."))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Firewall flush note: {e}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully wiped:\n"
                f" - {s_count} Sessions\n"
                f" - {c_count} Coin Events\n"
                f" - {t_count} Transactions\n"
                f" - {d_count} Device Profiles\n"
                f" - {g_count} Session Groups\n"
                f"All user history has been cleanly reset to 0!"
            )
        )
