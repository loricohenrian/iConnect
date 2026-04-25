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
