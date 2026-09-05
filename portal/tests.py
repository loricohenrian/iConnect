from django.test import TestCase
from django.urls import reverse

from sessions_app.models import Plan


class PortalProductionTests(TestCase):
    def setUp(self):
        Plan.objects.create(
            name="P5",
            price=5,
            duration_minutes=30,
            speed_limit=None,
            is_active=True,
        )

    def test_index_shows_production_flow_only(self):
        """Dev mode form is removed; only production coin slot flow exists."""
        response = self.client.get(reverse("portal:index"))

        self.assertEqual(response.status_code, 200)
        # Dev mode must never appear
        self.assertNotContains(response, "Development Mode")
        self.assertNotContains(response, 'id="dev-start-form"', html=False)
        # Production flow elements must be present
        self.assertContains(response, 'id="request-slot-btn"', html=False)
        self.assertContains(response, 'id="start-session-btn"', html=False)
        self.assertContains(response, 'id="start-flow-message"', html=False)

    def test_report_issue_success(self):
        """Users can submit issue reports via API."""
        response = self.client.post(
            reverse("portal:api_report_issue"),
            data={
                "category": "coin_stuck",
                "message": "P5 inserted but no credit registered",
                "contact_info": "09123456789",
                "mac_address": "11:22:33:44:55:66",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")

        from dashboard.models import IssueReport
        report = IssueReport.objects.get(id=data["report_id"])
        self.assertEqual(report.category, "coin_stuck")
        self.assertEqual(report.message, "P5 inserted but no credit registered")
        self.assertEqual(report.mac_address, "11:22:33:44:55:66")
        self.assertEqual(report.status, "pending")

    def test_report_issue_empty_message_error(self):
        """Empty message should return 400."""
        response = self.client.post(
            reverse("portal:api_report_issue"),
            data={"category": "other", "message": ""},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class RatesModalTests(TestCase):
    def setUp(self):
        Plan.objects.create(name="₱1 Plan", price=1, duration_minutes=10, is_active=True)
        Plan.objects.create(name="₱5 Plan", price=5, duration_minutes=60, is_active=True)
        Plan.objects.create(name="₱10 Plan", price=10, duration_minutes=150, is_active=True)
        Plan.objects.create(name="₱20 Plan", price=20, duration_minutes=360, is_active=True)

    def test_index_renders_smart_combo_card_and_no_bottom_close_btn(self):
        response = self.client.get(reverse("portal:index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("smart_combo_examples", response.context)
        self.assertEqual(len(response.context["smart_combo_examples"]), 3)

        # Smart combo card should exist
        self.assertContains(response, 'class="smart-combo-card')
        self.assertContains(response, "Smart Combo Rates")
        self.assertContains(response, "₱7")
        self.assertContains(response, "₱5 Plan + two ₱1 Plans")
        self.assertContains(response, "1h 20m")

        # Bottom Close button should NOT exist
        self.assertNotContains(response, '>Close</button>')

    def test_live_data_returns_smart_combos(self):
        response = self.client.get("/api/portal/live-data/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("smart_combo_examples", data)
        self.assertIn("smart_combo_examples_extend", data)
        self.assertEqual(len(data["smart_combo_examples"]), 3)
        self.assertEqual(data["smart_combo_examples"][0]["amount"], 7)
        self.assertEqual(data["smart_combo_examples_extend"][0]["duration"], "+1h 20m")


