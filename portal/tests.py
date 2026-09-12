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
        self.assertContains(response, 'id="btn-cancel-coin-request"', html=False)
        self.assertContains(response, 'id="link-cancel-coin-request"', html=False)
        # Cancel confirmation modal elements must be present
        self.assertContains(response, 'id="cancelCoinModal"', html=False)
        self.assertContains(response, 'id="btn-abort-cancel-coin"', html=False)
        self.assertContains(response, 'id="btn-confirm-cancel-coin"', html=False)

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
        self.assertContains(response, "Speed Note:")

        # Bottom Close button should NOT exist
        self.assertNotContains(response, '>Close</button>')

        # View WiFi Rates button should have SVG and no clipboard emoji
        self.assertContains(response, 'id="btn-view-rates"')
        self.assertContains(response, 'View Wifi Rates')
        self.assertNotContains(response, 'View WiFi Rates & Speeds 📋')

        # Group plan modal should use Insert Coins
        self.assertContains(response, 'id="btn-group-request-slot"')
        self.assertContains(response, 'Insert Coins')
        self.assertNotContains(response, 'Insert Coins 🪙')
        self.assertNotContains(response, 'Request Coin Slot')

        # STUDYING WITH CLASSMATES text should be removed
        self.assertNotContains(response, 'STUDYING WITH CLASSMATES?')

    def test_session_page_removes_unwanted_notices(self):
        from sessions_app.models import Session
        session = Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:01",
            duration_minutes_purchased=60,
            amount_paid=5,
            status="active",
        )
        response = self.client.get(f"/session/?mac={session.mac_address}")
        self.assertEqual(response.status_code, 200)

        # Removed notices
        self.assertNotContains(response, "Extending your session automatically renews your pause allowance")
        self.assertNotContains(response, "Renews Pauses")
        self.assertNotContains(response, "STUDYING WITH CLASSMATES?")
        self.assertNotContains(response, "View WiFi Rates & Speeds 📋")

        # View rates button inside extend section
        self.assertContains(response, 'id="btn-view-rates"')
        self.assertContains(response, 'View Wifi Rates')

        # Group modal in session page uses Insert Coins
        self.assertContains(response, 'id="btn-group-request-slot"')
        self.assertContains(response, 'Insert Coins')
        self.assertNotContains(response, 'Insert Coins 🪙')

        # Progressive extend buttons and cancel controls
        self.assertContains(response, 'id="extend-request-btn"')
        self.assertContains(response, 'id="extend-now-btn"')
        self.assertContains(response, 'id="btn-cancel-coin-request"')
        self.assertContains(response, 'id="link-cancel-coin-request"')

    def test_live_data_returns_smart_combos(self):
        response = self.client.get("/api/portal/live-data/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("smart_combo_examples", data)
        self.assertIn("smart_combo_examples_extend", data)
        self.assertEqual(len(data["smart_combo_examples"]), 3)
        self.assertEqual(data["smart_combo_examples"][0]["amount"], 7)
        self.assertEqual(data["smart_combo_examples_extend"][0]["duration"], "+1h 20m")

    def test_session_page_renders_popular_plan_badge(self):
        from sessions_app.models import Session
        p5 = Plan.objects.get(name="₱5 Plan")
        for i in range(3):
            Session.objects.create(
                mac_address=f"AA:BB:CC:DD:00:0{i}",
                plan=p5,
                duration_minutes_purchased=60,
                amount_paid=5,
                status="expired",
            )
        active_session = Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:02",
            plan=p5,
            duration_minutes_purchased=60,
            amount_paid=5,
            status="active",
        )
        response = self.client.get(f"/session/?mac={active_session.mac_address}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["most_popular_plan_id"], p5.id)
        self.assertContains(response, '<div class="plan-popular">Popular</div>')

        # Also verify live_data marks is_most_popular for p5
        live_resp = self.client.get("/api/portal/live-data/")
        self.assertEqual(live_resp.status_code, 200)
        plans_data = live_resp.json()["plans"]
        p5_data = next(p for p in plans_data if p["id"] == p5.id)
        self.assertTrue(p5_data["is_most_popular"])

    def test_manual_page_content(self):
        response = self.client.get("/manual/")
        self.assertEqual(response.status_code, 200)

        # Solo plan updated steps
        self.assertContains(response, 'Tap "Insert Coins"')
        self.assertContains(response, 'View WiFi Rates & Speeds')
        self.assertContains(response, 'Connect Now')
        self.assertContains(response, 'Pause or Extend Anytime')

        # Tagalog steps
        self.assertContains(response, 'I-tap ang "Insert Coins"')
        self.assertContains(response, 'Maghulog ng Barya')
        self.assertContains(response, 'I-tap ang "Connect Now"')
        self.assertContains(response, 'I-Pause o Mag-Extend Anumang Oras')

        # Group plan 5-digit code assertions
        self.assertContains(response, '5-digit Group Code')
        self.assertContains(response, '5-digit na Group Code')
        self.assertNotContains(response, '6-digit')

        # Obsolete request coin slot should NOT exist anywhere in manual
        self.assertNotContains(response, 'Request Coin Slot')


class SessionExpirationTests(TestCase):
    def setUp(self):
        from sessions_app.models import Session, Plan
        from django.utils import timezone
        from datetime import timedelta
        self.plan = Plan.objects.create(name="₱5 Plan", price=5, duration_minutes=30, is_active=True)
        # Create a session that has run out of time (started 31 mins ago for a 30 min plan)
        self.session = Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:99",
            plan=self.plan,
            duration_minutes_purchased=30,
            amount_paid=5,
            status="active",
            time_in=timezone.now() - timedelta(minutes=31),
        )

    def test_session_status_expires_depleted_session(self):
        """API /api/session/status/ expires sessions with remaining time <= 1s."""
        response = self.client.get(f"/api/session/status/?mac_address={self.session.mac_address}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "expired")
        self.assertEqual(data["time_remaining_seconds"], 0)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "expired")

    def test_portal_index_expires_depleted_session_and_does_not_redirect(self):
        """Visiting / does not redirect back to /session/ if session is expired or depleted."""
        response = self.client.get(f"/?mac={self.session.mac_address}")
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "expired")

    def test_portal_session_page_redirects_expired_session(self):
        """Visiting /session/ redirects to /?expired=1 if session is depleted."""
        response = self.client.get(f"/session/?mac={self.session.mac_address}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("expired=1", response.url)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "expired")

    def test_portal_session_page_renders_cancel_coin_modal(self):
        """Active session page renders cancelCoinModal and its confirmation buttons."""
        from django.utils import timezone
        from datetime import timedelta
        active_sess = self.session
        active_sess.status = "active"
        active_sess.time_in = timezone.now() - timedelta(minutes=5)
        active_sess.save(update_fields=["status", "time_in"])

        response = self.client.get(f"/session/?mac={active_sess.mac_address}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cancelCoinModal"')
        self.assertContains(response, 'id="btn-abort-cancel-coin"')
        self.assertContains(response, 'id="btn-confirm-cancel-coin"')



class CaptivePortalRedirectionTests(TestCase):
    def test_probe_domain_redirects_to_portal_ip(self):
        """Requests with external host (e.g. connectivitycheck.gstatic.com) redirect to http://10.10.10.1/"""
        response = self.client.get("/", HTTP_HOST="connectivitycheck.gstatic.com")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("http://10.10.10.1/"))

    def test_portal_ip_renders_index_without_redirect_loop(self):
        """Requests directly to 10.10.10.1 render index (HTTP 200) without redirecting to http:///"""
        response = self.client.get("/", HTTP_HOST="10.10.10.1")
        self.assertEqual(response.status_code, 200)

    def test_captive_portal_probe_endpoint_redirects_unauthenticated(self):
        """Unauthenticated /generate_204 probe redirects to http://10.10.10.1/"""
        response = self.client.get("/generate_204", HTTP_HOST="connectivitycheck.gstatic.com")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("http://10.10.10.1/"))
        self.assertEqual(response.headers.get("Connection"), "close")

    def test_captive_portal_probe_endpoint_succeeds_authenticated(self):
        """Authenticated /generate_204 probe returns 204 No Content to validate network."""
        from sessions_app.models import Session, Plan
        plan = Plan.objects.create(name="₱5 Plan", price=5, duration_minutes=60, is_active=True)
        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:77",
            plan=plan,
            duration_minutes_purchased=60,
            amount_paid=5,
            status="active",
        )
        response = self.client.get("/generate_204", HTTP_X_MAC_ADDRESS="AA:BB:CC:DD:EE:77", HTTP_HOST="connectivitycheck.gstatic.com")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("Connection"), "close")

    def test_captive_portal_api_unauthenticated(self):
        """Unauthenticated RFC 8908 query returns captive: true and portal URL."""
        response = self.client.get("/api/captive-portal/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "application/captive+json")
        data = response.json()
        self.assertTrue(data["captive"])
        self.assertIn("10.10.10.1", data["user-portal-url"])
        self.assertEqual(data["seconds-remaining"], 0)

    def test_captive_portal_api_authenticated(self):
        """Authenticated RFC 8908 query returns captive: false and session URL."""
        from sessions_app.models import Session, Plan
        plan = Plan.objects.create(name="₱10 Plan", price=10, duration_minutes=120, is_active=True)
        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:88",
            plan=plan,
            duration_minutes_purchased=120,
            amount_paid=10,
            status="active",
        )
        response = self.client.get("/api/captive-portal/", HTTP_X_MAC_ADDRESS="AA:BB:CC:DD:EE:88")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "application/captive+json")
        data = response.json()
        self.assertFalse(data["captive"])
        self.assertIn("/session/", data["user-portal-url"])
        self.assertGreater(data["seconds-remaining"], 0)







