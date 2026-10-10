from datetime import datetime, timedelta, date
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, RequestFactory
from django.utils import timezone

from .analytics import analytics_dates, revenue_benchmark
from .views import analytics_view
from sessions_app.models import CoinEvent, Session


class BenchmarkDatesTests(SimpleTestCase):
    today = date(2026, 10, 7)

    def test_last_thirty_days_has_thirty_inclusive_dates(self):
        period, start, end = analytics_dates({}, self.today)
        self.assertEqual(period, 'month')
        self.assertEqual((end - start).days + 1, 30)

    def test_aliases_use_their_intended_periods(self):
        for alias, canonical in [('weekly', 'week'), ('monthly', 'month'),
                                 ('yearly', 'year'), ('all_time', 'all'), ('daily', 'today')]:
            self.assertEqual(analytics_dates({'period': alias}, self.today),
                             analytics_dates({'period': canonical}, self.today))

    def test_custom_dates_are_swapped_and_legacy_parameters_supported(self):
        self.assertEqual(analytics_dates({'period': 'custom', 'custom_start': '2026-10-05',
                                         'custom_end': '2026-10-01'}, self.today),
                         ('custom', date(2026, 10, 1), date(2026, 10, 5)))

    def test_invalid_or_unbounded_ranges_fall_back_to_explicit_thirty_days(self):
        for params in [{'period': 'unknown'}, {'period': 'custom'},
                       {'period': 'custom', 'start_date': '2026-99-99', 'end_date': '2026-10-01'},
                       {'period': 'custom', 'start_date': '2026-10-08', 'end_date': '2026-10-09'}]:
            self.assertEqual(analytics_dates(params, self.today), analytics_dates({}, self.today))


class RevenueBenchmarkTests(TestCase):
    def setUp(self):
        self.tz = ZoneInfo('Asia/Manila')
        self.now = datetime(2026, 10, 7, 12, tzinfo=self.tz)
        override = timezone.override(self.tz)
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)

    def coin(self, amount, stamp):
        return CoinEvent.objects.create(amount=amount, denomination=1, timestamp=stamp)

    def benchmark(self, period='today', **dates):
        period, start, end = analytics_dates({'period': period, **dates}, self.now.date())
        return revenue_benchmark(period, start, end, self.now)

    def test_zero_previous_revenue_never_fabricates_one_hundred_percent(self):
        self.coin(100, self.now)
        result = self.benchmark()
        self.assertIsNone(result['revenue_growth'])
        self.assertFalse(result['growth_comparable'])
        self.assertEqual(result['growth_previous_revenue'], 0)

    def test_empty_database_has_no_percentage_baseline(self):
        self.assertIsNone(self.benchmark()['revenue_growth'])

    def test_percentage_formula_for_increase_decrease_and_no_change(self):
        prior = self.coin(100, self.now - timedelta(days=1))
        current = self.coin(150, self.now)
        self.assertEqual(self.benchmark()['revenue_growth'], 50)
        current.amount = 50
        current.save(update_fields=['amount'])
        self.assertEqual(self.benchmark()['revenue_growth'], -50)
        current.amount = prior.amount
        current.save(update_fields=['amount'])
        self.assertEqual(self.benchmark()['revenue_growth'], 0)
        current.delete()
        self.assertEqual(self.benchmark()['revenue_growth'], -100)

    def test_today_matches_yesterdays_elapsed_local_time_not_full_day(self):
        self.coin(100, self.now - timedelta(days=1, hours=1))
        self.coin(900, self.now - timedelta(days=1) + timedelta(hours=1))
        self.coin(150, self.now)
        self.coin(900, self.now + timedelta(hours=1))
        result = self.benchmark()
        self.assertEqual(result['growth_current_revenue'], 150)
        self.assertEqual(result['growth_previous_revenue'], 100)
        self.assertEqual(result['revenue_growth'], 50)

    def test_week_to_date_compares_same_weekdays_and_excludes_later_days(self):
        monday = self.now - timedelta(days=2)
        self.coin(100, monday - timedelta(days=7))
        self.coin(900, self.now - timedelta(days=7) + timedelta(hours=1))
        self.coin(900, self.now - timedelta(days=6))
        self.coin(150, monday)
        self.coin(900, self.now + timedelta(days=1))
        result = self.benchmark('week')
        self.assertEqual(result['growth_previous_start'], date(2026, 9, 28))
        self.assertEqual(result['growth_previous_end'], date(2026, 9, 30))
        self.assertEqual(result['revenue_growth'], 50)

    def test_year_to_date_does_not_compare_against_entire_previous_year(self):
        self.coin(100, datetime(2025, 1, 1, 12, tzinfo=self.tz))
        self.coin(900, datetime(2025, 12, 1, 12, tzinfo=self.tz))
        self.coin(150, self.now)
        result = self.benchmark('year')
        self.assertEqual(result['growth_previous_end'], date(2025, 10, 7))
        self.assertEqual(result['revenue_growth'], 50)

    def test_leap_day_matches_february_twenty_eight_without_error(self):
        self.now = datetime(2024, 2, 29, 12, tzinfo=self.tz)
        self.coin(100, datetime(2023, 2, 28, 12, tzinfo=self.tz))
        self.coin(150, self.now)
        result = self.benchmark('year')
        self.assertEqual(result['growth_previous_end'], date(2023, 2, 28))
        self.assertEqual(result['revenue_growth'], 50)

    def test_month_uses_adjacent_nonoverlapping_thirty_day_windows(self):
        self.coin(900, datetime(2026, 8, 8, 12, tzinfo=self.tz))
        self.coin(10, datetime(2026, 8, 9, 0, tzinfo=self.tz))
        self.coin(10, datetime(2026, 9, 7, 12, tzinfo=self.tz))
        self.coin(30, datetime(2026, 9, 8, 0, tzinfo=self.tz))
        result = self.benchmark('month')
        self.assertEqual(result['growth_previous_start'], date(2026, 8, 9))
        self.assertEqual(result['growth_previous_end'], date(2026, 9, 7))
        self.assertEqual(result['growth_current_revenue'], 30)
        self.assertEqual(result['growth_previous_revenue'], 20)
        self.assertEqual(result['revenue_growth'], 50)

    def test_historical_custom_range_compares_full_days_not_current_time(self):
        self.coin(100, datetime(2026, 10, 1, 23, 59, tzinfo=self.tz))
        self.coin(150, datetime(2026, 10, 4, 23, 59, tzinfo=self.tz))
        result = self.benchmark('custom', start_date='2026-10-04', end_date='2026-10-06')
        self.assertEqual(result['growth_previous_start'], date(2026, 10, 1))
        self.assertEqual(result['growth_previous_end'], date(2026, 10, 3))
        self.assertEqual(result['revenue_growth'], 50)

    def test_coin_ledger_is_not_double_counted_with_paid_sessions(self):
        self.coin(100, self.now - timedelta(days=1))
        self.coin(150, self.now)
        Session.objects.create(mac_address='02:00:00:00:00:01', amount_paid=900,
                               duration_minutes_purchased=60, initial_bandwidth_mb=0, time_in=self.now)
        self.assertEqual(self.benchmark()['growth_current_revenue'], 150)

    def test_legacy_sessions_fallback_counts_paid_but_not_free_compensation(self):
        for index, (stamp, amount) in enumerate([(self.now - timedelta(days=1), 100),
                                               (self.now, 150), (self.now, 0)]):
            Session.objects.create(mac_address=f'02:00:00:00:00:0{index}', amount_paid=amount,
                                   duration_minutes_purchased=60, initial_bandwidth_mb=0, time_in=stamp)
        self.assertEqual(self.benchmark()['revenue_growth'], 50)

    def test_all_time_shows_total_not_fake_growth(self):
        self.coin(100, self.now)
        self.coin(900, self.now + timedelta(days=1))
        result = self.benchmark('all')
        self.assertEqual(result['growth_current_revenue'], 100)
        self.assertIsNone(result['revenue_growth'])
        self.assertIsNone(result['growth_previous_revenue'])

    def test_rendered_page_shows_na_and_preserves_custom_dates(self):
        self.coin(100, self.now)
        admin = get_user_model().objects.create_user(username='benchmark_admin', is_staff=True)
        request = RequestFactory().get('/iconnect-ops/analytics/',
                                       {'period': 'custom', 'start_date': '2026-10-07', 'end_date': '2026-10-07'})
        request.user = admin
        with patch('django.utils.timezone.now', return_value=self.now):
            response = analytics_view(request)
        self.assertContains(response, 'N/A')
        self.assertContains(response, 'No previous revenue')
        self.assertNotContains(response, '+100%')
        self.assertNotContains(response, 'Growing Momentum')
        self.assertContains(response, 'value="2026-10-07"')
        self.assertContains(response, r"fetchRevenueData('custom', '2026\u002D10\u002D07', '2026\u002D10\u002D07')")
