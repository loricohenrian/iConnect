from pathlib import Path

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import ReportDeliveryLog
from .tasks import generate_and_deliver_daily_report


@override_settings(
    PISONET_DAILY_REPORT_SEND_EMAIL=False,
    PISONET_DAILY_REPORT_RECIPIENTS=[],
)
class ReportPipelineTests(TestCase):
    def test_generate_and_deliver_daily_report_saves_files_and_log(self):
        result = generate_and_deliver_daily_report()

        self.assertEqual(result["status"], "success")
        self.assertIn("pdf_file_path", result)
        self.assertIn("csv_file_path", result)

        pdf_path = Path(result["pdf_file_path"])
        csv_path = Path(result["csv_file_path"])

        self.assertTrue(pdf_path.exists())
        self.assertTrue(csv_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 0)
        self.assertGreater(csv_path.stat().st_size, 0)

        today = timezone.localdate()
        log = ReportDeliveryLog.objects.filter(report_type="daily", report_date=today).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, "success")
        self.assertFalse(log.email_sent)
        self.assertTrue(log.pdf_file_path)
        self.assertTrue(log.csv_file_path)


class ReportAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.password = "admin123"
        self.user = User.objects.create_user(
            username="report_admin",
            password=self.password,
            is_staff=True,
            is_superuser=True,
        )

    def test_generate_report_requires_authentication(self):
        response = self.client.get("/reports/generate/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/iconnect-ops/login/", response.url)

    def test_generate_report_allows_staff(self):
        logged_in = self.client.login(username=self.user.username, password=self.password)
        self.assertTrue(logged_in)

        # Create test session with no plan (custom coin session)
        from sessions_app.models import Session
        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:FF",
            ip_address="10.10.10.55",
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            plan=None,
        )

        for format_type in ["csv", "pdf"]:
            for period in ["today", "week", "month"]:
                response = self.client.get(f"/reports/generate/?type=weekly&period={period}&format={format_type}")
                self.assertEqual(response.status_code, 200)

            # Test custom date range
            custom_resp = self.client.get(f"/reports/generate/?type=custom&period=custom&format={format_type}&start_date=2026-08-01&end_date=2026-08-31")
            self.assertEqual(custom_resp.status_code, 200)


