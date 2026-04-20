from django.test import TestCase, override_settings
from django.urls import reverse

from sessions_app.models import Plan


class PortalDevFlowVisibilityTests(TestCase):
    def setUp(self):
        Plan.objects.create(
            name="P5",
            price=5,
            duration_minutes=30,
            speed_limit=None,
            is_active=True,
        )

    @override_settings(DEBUG=False, PISONET_PORTAL_DEV_FLOW_ENABLED=False)
    def test_index_hides_dev_manual_flow_in_production(self):
        response = self.client.get(reverse("portal:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Development Mode — Simulate Coin Insert")
        self.assertNotContains(response, "id=\"dev-start-form\"", html=False)
        self.assertContains(response, "id=\"request-slot-btn\"", html=False)
        self.assertContains(response, "id=\"start-session-btn\"", html=False)
        self.assertContains(response, "id=\"start-flow-message\"", html=False)

    @override_settings(DEBUG=False, PISONET_PORTAL_DEV_FLOW_ENABLED=True)
    def test_index_shows_dev_manual_flow_when_explicitly_enabled(self):
        response = self.client.get(reverse("portal:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Development Mode — Simulate Coin Insert")
        self.assertContains(response, "id=\"dev-start-form\"", html=False)
