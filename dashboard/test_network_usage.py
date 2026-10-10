"""The overview's MB total is a start-date cohort, not traffic used today."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate

from .views import overview, dashboard_stats_api
from sessions_app.views import bandwidth_usage
from sessions_app.models import Session


class NetworkUsageLabelTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 9, 16, 5, tzinfo=dt_timezone.utc)  # Oct 10, 00:05 Manila
        self.clock = patch('django.utils.timezone.now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.admin = get_user_model().objects.create_user(username='network_admin', is_staff=True)
        for index, status in enumerate(('active', 'paused', 'expired'), 1):
            Session.objects.create(
                mac_address=f'02:00:00:00:00:0{index}', time_in=self.now,
                device_name='Network test', amount_paid=0,
                duration_minutes_purchased=180, status=status,
                paused_at=self.now if status == 'paused' else None,
                initial_bandwidth_mb=0, bandwidth_used_mb=100 * index)
        Session.objects.create(
            mac_address='02:00:00:00:00:04', time_in=self.now - timedelta(minutes=10),
            device_name='Yesterday test', amount_paid=0,
            duration_minutes_purchased=180, status='active',
            initial_bandwidth_mb=0, bandwidth_used_mb=900)

    def request(self, path):
        request = APIRequestFactory().get(path)
        force_authenticate(request, user=self.admin)
        return request

    def test_overview_labels_saved_cohort_not_live_speed(self):
        request = RequestFactory().get('/iconnect-ops/')
        request.user = self.admin
        response = overview(request)
        self.assertContains(response, 'Network Overview')
        self.assertContains(response, 'Data Used')
        self.assertContains(response, 'Sessions started today only.')
        self.assertContains(response, 'not Mbps')
        self.assertContains(response, '600.0 MB')
        self.assertNotContains(response, 'Bandwidth In Use')

    def test_bandwidth_api_includes_all_today_statuses_but_not_yesterdays_session(self):
        with patch('sessions_app.bandwidth.get_all_device_bandwidth_mb', return_value=[]), \
                patch('sessions_app.bandwidth.get_live_throughput_mbps', return_value={'by_mac': {}, 'total_mbps': 0}):
            response = bandwidth_usage(self.request('/api/bandwidth/'))
        self.assertEqual(response.data['bandwidth_today_mb'], 600)
        self.assertEqual(response.data['total_bandwidth_mb'], 0)

    def test_dashboard_stats_uses_same_saved_cohort(self):
        with patch('sessions_app.tasks.cleanup_expired_and_stale_sessions', return_value=0):
            response = dashboard_stats_api(self.request('/api/dashboard/stats/'))
        self.assertEqual(response.data['bandwidth_today_mb'], 600)
