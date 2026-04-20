"""
iConnect — Seed Data Management Command
Creates sample plans, sessions, costs, and announcements for development.
"""
import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from sessions_app.models import Plan, Session, CoinEvent, WhitelistedDevice
from dashboard.models import Announcement, RevenueGoal, ProjectCost, DailyRevenueSummary


class Command(BaseCommand):
    help = 'Populate database with sample data for development'

    def handle(self, *args, **options):
        self.stdout.write('[*] Seeding iConnect database...\n')

        # Create Plans
        self.stdout.write('  Creating plans...')
        plans_data = [
            {'name': '₱1 Plan', 'price': 1, 'duration_minutes': 6, 'speed_limit': None},
            {'name': '₱5 Plan', 'price': 5, 'duration_minutes': 30, 'speed_limit': None},
            {'name': '₱10 Plan', 'price': 10, 'duration_minutes': 60, 'speed_limit': None},
            {'name': '₱20 Plan', 'price': 20, 'duration_minutes': 120, 'speed_limit': None},
        ]
        plans = []
        for data in plans_data:
            plan, created = Plan.objects.get_or_create(
                price=data['price'],
                defaults=data
            )
            plans.append(plan)
            if created:
                self.stdout.write(f'    [+] {plan.name}')

        # Create Project Costs
        self.stdout.write('\n  Creating project costs...')
        costs_data = [
            {'description': 'ALLAN H3 1GB Board', 'amount': 2500},
            {'description': 'ALLAN 1239A Pro Max Coin Acceptor', 'amount': 1800},
            {'description': 'PisoWiFi Custom Board 5V', 'amount': 800},
            {'description': 'Converge Fiber Router/ONT', 'amount': 0},
            {'description': '50W Solar Panel', 'amount': 2800},
            {'description': '10A Charge Controller', 'amount': 600},
            {'description': '12V 20Ah AGM Battery', 'amount': 2500},
            {'description': 'ALLAN Metal Piso WiFi Box', 'amount': 3500},
            {'description': 'LAN Cables & Connectors', 'amount': 300},
            {'description': 'Dupont Wires & Misc', 'amount': 200},
        ]
        for data in costs_data:
            cost, created = ProjectCost.objects.get_or_create(
                description=data['description'],
                defaults=data
            )
            if created:
                self.stdout.write(f'    [+] {cost.description}: P{cost.amount}')

        # Create Revenue Goals
        self.stdout.write('\n  Creating revenue goals...')
        RevenueGoal.objects.get_or_create(
            period='daily',
            defaults={'target_amount': 200}
        )
        RevenueGoal.objects.get_or_create(
            period='weekly',
            defaults={'target_amount': 1200}
        )

        # Create Announcements
        self.stdout.write('\n  Creating announcements...')
        announcements = [
            'Welcome to iConnect! Insert coins to start browsing.',
            'WiFi maintenance scheduled for Sunday 10:00 PM — 11:00 PM.',
        ]
        for msg in announcements:
            Announcement.objects.get_or_create(
                message=msg,
                defaults={'is_active': True}
            )

        # Create Whitelisted Devices
        self.stdout.write('\n  Creating whitelisted devices...')
        WhitelistedDevice.objects.get_or_create(
            mac_address='AA:11:22:33:44:55',
            defaults={'device_name': 'Admin Laptop', 'added_by': 'admin'}
        )

        # Create Sample Sessions (past 14 days)
        self.stdout.write('\n  Creating sample sessions...')
        mac_addresses = [
            'B0:A7:B9:C1:D2:E3', 'F4:96:34:A8:B7:C6',
            'D8:3A:DD:45:67:89', '1C:87:2C:AB:CD:EF',
            'E0:5A:1B:98:76:54', '3C:22:FB:12:34:56',
            '48:A4:72:PP:QQ:RR', '7C:B9:4A:11:22:33',
        ]
        device_names = [
            'Samsung Galaxy A15', 'iPhone 13', 'Vivo V29',
            'Realme GT', 'Xiaomi Redmi 13', 'OPPO A78',
            'Samsung Galaxy S23', 'Huawei Nova',
        ]

        now = timezone.now()
        session_count = 0

        for day_offset in range(14, 0, -1):
            day = now - timedelta(days=day_offset)
            # Random number of sessions per day (3-15)
            num_sessions = random.randint(3, 15)

            for _ in range(num_sessions):
                plan = random.choice(plans)
                mac_idx = random.randint(0, len(mac_addresses) - 1)
                hour = random.choice([7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17])
                minute = random.randint(0, 59)

                time_in = day.replace(hour=hour, minute=minute, second=0)
                time_out = time_in + timedelta(minutes=plan.duration_minutes)

                Session.objects.create(
                    mac_address=mac_addresses[mac_idx],
                    plan=plan,
                    time_in=time_in,
                    time_out=time_out,
                    duration_minutes_purchased=plan.duration_minutes,
                    remaining_minutes=0,
                    amount_paid=plan.price,
                    status='expired',
                    bandwidth_used_mb=round(random.uniform(5, 150), 1),
                    ip_address=f'192.168.1.{random.randint(100, 254)}',
                    device_name=device_names[mac_idx],
                )

                # Create coin event
                CoinEvent.objects.create(
                    amount=plan.price,
                    denomination=plan.price,
                    timestamp=time_in - timedelta(seconds=30),
                )

                session_count += 1

        self.stdout.write(f'    [+] Created {session_count} sample sessions\n')

        # Generate daily summaries
        self.stdout.write('  Generating daily summaries...')
        for day_offset in range(14, 0, -1):
            day = (now - timedelta(days=day_offset)).date()
            day_sessions = Session.objects.filter(time_in__date=day, status='expired')
            total_rev = day_sessions.aggregate(t=Sum('amount_paid'))['t'] or 0
            total_sess = day_sessions.count()

            from django.db.models.functions import ExtractHour
            peak = day_sessions.annotate(
                hour=ExtractHour('time_in')
            ).values('hour').annotate(
                count=Count('id')
            ).order_by('-count').first()

            DailyRevenueSummary.objects.update_or_create(
                date=day,
                defaults={
                    'total_revenue': total_rev,
                    'total_sessions': total_sess,
                    'avg_session_minutes': day_sessions.aggregate(
                        a=Avg('duration_minutes_purchased')
                    )['a'] or 0,
                    'peak_hour': peak['hour'] if peak else None,
                }
            )

        self.stdout.write(self.style.SUCCESS('\n[OK] Seed data created successfully!'))
        self.stdout.write(f'   Total project cost: P{ProjectCost.total_cost():,}')
        self.stdout.write(f'   Total sessions: {Session.objects.count()}')
        self.stdout.write(f'   Total revenue: P{Session.objects.aggregate(t=Sum("amount_paid"))["t"] or 0:,}')


