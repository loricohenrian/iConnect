"""Report outputs use isolated records, fixed local dates and no hardware."""
import csv
import io
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from dashboard.models import DailyRevenueSummary, OperatingExpense, ProjectCost
from sessions_app.models import CoinEvent, Plan, PurchaseTransaction, Session
from .web_exports import REPORT_TZ, csv_text, daily_revenue_csv, report_csv, report_data, report_pdf


class WebReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.now = datetime(2026, 10, 20, 3, 0, tzinfo=dt_timezone.utc)
        for name, value in (("django.utils.timezone.now", self.now),
                            ("sessions_app.power_recovery.ensure_power_recovery", None)):
            patcher = patch(name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_user("report-review", is_staff=True)
        self.client.force_login(self.admin)
        self.plan = Plan.objects.create(name="P20 Plan", price=20, duration_minutes=300)
        self.old_session = Session.objects.create(
            mac_address="02:00:00:00:00:01", plan=self.plan, ip_address="10.10.10.20",
            time_in=self.now - timedelta(days=10), amount_paid=999,
            duration_minutes_purchased=300, initial_bandwidth_mb=0, status="active",
        )
        self.current_session = Session.objects.create(
            mac_address="02:00:00:00:00:02", plan=self.plan, ip_address="10.10.10.21",
            time_in=self.now, amount_paid=100, duration_minutes_purchased=1500,
            initial_bandwidth_mb=0, status="paused", remaining_minutes=1500,
        )
        CoinEvent.objects.create(amount=20, denomination=20, timestamp=self.now, session=self.old_session)
        CoinEvent.objects.create(amount=5, denomination=5, timestamp=self.now, mac_address=None)
        PurchaseTransaction.objects.create(amount=20, plan=self.plan, session=self.old_session, timestamp=self.now)

    def rows(self, response):
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_coin_revenue_counts_extensions_and_unassigned_coins_not_session_amounts(self):
        data = report_data()
        self.assertEqual(data["revenue"], 25)
        self.assertEqual(data["coin_events"], 2)
        self.assertEqual(data["total_sessions"], 1)
        self.assertEqual(data["purchased_minutes"], 1500)
        self.assertEqual(data["purchase_revenue"], 20)
        self.assertEqual(sum(row["revenue"] for row in data["daily"]), data["revenue"])

    def test_daily_boundaries_are_philippine_midnight_and_exclude_future_records(self):
        midnight = datetime(2026, 10, 20, tzinfo=REPORT_TZ)
        CoinEvent.objects.create(amount=1, denomination=1, timestamp=midnight)
        CoinEvent.objects.create(amount=10, denomination=10, timestamp=midnight - timedelta(seconds=1))
        CoinEvent.objects.create(amount=20, denomination=20, timestamp=self.now + timedelta(seconds=1))
        self.assertEqual(report_data()["revenue"], 26)

    def test_period_lengths_include_today_and_zero_revenue_dates(self):
        for period, days in (("today", 1), ("week", 7), ("month", 30)):
            with self.subTest(period=period):
                data = report_data(period)
                self.assertEqual(len(data["daily"]), days)
                self.assertEqual(data["daily"][-1]["date"].isoformat(), "2026-10-20")
                self.assertEqual(data["revenue"], 25)

    def test_explicit_period_wins_over_default_daily_type(self):
        response = self.client.get("/reports/generate/?period=week&format=print")
        self.assertContains(response, "Last 7 days")
        self.assertContains(response, "Oct 14, 2026 - Oct 20, 2026")

    def test_weekly_type_without_period_resolves_week(self):
        self.assertContains(self.client.get("/reports/generate/?type=weekly&format=print"), "Last 7 days")

    def test_custom_dates_are_inclusive_and_inverted_dates_are_normalized(self):
        data = report_data("custom", "2026-10-20", "2026-10-18")
        self.assertEqual(len(data["daily"]), 3)
        self.assertEqual(data["start_date"].isoformat(), "2026-10-18")
        self.assertEqual(data["revenue"], 25)

    def test_invalid_or_excessive_ranges_return_400_without_export(self):
        for start, end in (("bad", "2026-10-20"), ("2026-02-31", "2026-10-20"),
                           ("2000-01-01", "2026-10-20"), ("9999-12-31", "9999-12-31")):
            for kind in ("print", "pdf", "csv"):
                with self.subTest(start=start, format=kind):
                    response = self.client.get("/reports/generate/", {"format": kind, "start_date": start, "end_date": end})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_plan_breakdown_uses_purchase_ledger_not_session_current_totals(self):
        row = report_data()["plans"][0]
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["revenue"], 20)
        self.assertEqual(row["share"], 100)

    def test_deleted_plan_does_not_lose_purchase_revenue(self):
        self.plan.delete()
        data = report_data()
        self.assertEqual(data["plans"][0]["name"], "Unspecified / deleted plan")
        self.assertEqual(data["purchase_revenue"], 20)

    def test_daily_csv_ignores_stale_summaries_and_includes_today(self):
        DailyRevenueSummary.objects.create(date=self.now.date(), total_revenue=9999)
        response = self.client.get("/iconnect-ops/revenue/export/")
        rows = self.rows(response)
        today_row = next(row for row in rows if row[0] == "2026-10-20")
        self.assertEqual(today_row[1:5], ["Tuesday", "25.00", "2", "1"])
        self.assertEqual(rows[-1][0:3], ["TOTAL", "", "25.00"])
        self.assertEqual(today_row[-2:], ["1500.00", "11:00"])
        self.assertTrue(all(len(row) == 10 for row in rows))

    def test_peak_hour_is_based_on_local_session_starts_with_earliest_tie(self):
        Session.objects.create(mac_address="02:00:00:00:00:03", time_in=self.now - timedelta(hours=3),
                               amount_paid=0, duration_minutes_purchased=30, initial_bandwidth_mb=0)
        self.assertEqual(report_data()["daily"][0]["peak_hour"], "08:00")
        Session.objects.create(mac_address="02:00:00:00:00:04", time_in=self.now,
                               amount_paid=0, duration_minutes_purchased=30, initial_bandwidth_mb=0)
        self.assertEqual(report_data()["daily"][0]["peak_hour"], "11:00")

    def test_report_csv_has_consistent_columns_metadata_units_and_reconciled_totals(self):
        response = report_csv(report_data("month"))
        rows = self.rows(response)
        self.assertTrue(all(len(row) == 11 for row in rows))
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(["Coin revenue", "25.00", "PHP"], [row[:3] for row in rows])
        self.assertIn(["TOTAL", "", "25.00"], [row[:3] for row in rows])
        self.assertIn("20261020", response["Content-Disposition"])
        self.assertIn("20260921", response["Content-Disposition"])

    def test_csv_text_sanitizes_formulas_even_after_leading_whitespace(self):
        for value in ("=1+1", " +cmd", "\t=SUM(1)", "\n@SUM(1)", "\r-1+2", "  @cmd"):
            with self.subTest(value=value):
                self.assertEqual(csv_text(value), "'" + value)
        self.assertEqual(csv_text("Normal, quoted \"text\""), "Normal, quoted \"text\"")

    def test_both_session_csvs_escape_untrusted_text_and_keep_numeric_amounts(self):
        self.current_session.device_name = '  =SUM(1,2)\n"device"'
        self.current_session.save(update_fields=["device_name"])
        self.plan.name = " +malicious"
        self.plan.save(update_fields=["name"])
        for path in ("/reports/generate/?format=csv", "/iconnect-ops/sessions/export/?period=all"):
            response = self.client.get(path)
            rows = self.rows(response)
            row = next(row for row in rows if row[1] == self.current_session.mac_address)
            self.assertEqual(row[3], "'" + self.current_session.device_name)
            self.assertEqual(row[4], "' +malicious")
            self.assertEqual(row[5], "100.00")
            self.assertIn("2026-10-20 11:00:00", row)

    def test_print_layout_has_no_dashboard_chrome_or_device_identifiers(self):
        response = self.client.get("/reports/generate/?format=print")
        self.assertContains(response, "@page")
        self.assertContains(response, "table-header-group")
        self.assertContains(response, "Print / Save as PDF")
        self.assertContains(response, "Asia/Manila")
        self.assertNotContains(response, 'class="sidebar"')
        self.assertNotContains(response, "<canvas")
        self.assertNotContains(response, self.current_session.mac_address)
        self.assertNotContains(response, self.current_session.ip_address)

    def test_all_time_print_keeps_financial_overview_with_explicit_estimate_basis(self):
        ProjectCost.objects.create(description="Hardware", amount=1000, date_added=self.now)
        OperatingExpense.objects.create(name="ISP", amount=300, period="monthly", date_added=self.now)
        data = report_data("all")
        self.assertEqual(data["financial"]["capital"], 1000)
        self.assertEqual(data["financial"]["expenses"], 110)
        self.assertEqual(data["financial"]["surplus"], -85)
        response = self.client.get("/reports/generate/?period=all&format=print")
        self.assertContains(response, "All-time financial overview")
        self.assertContains(response, "not a ledger of paid bills")
        self.assertContains(response, "Total Capital Investment", count=1)
        self.assertNotContains(response, "Hardware")
        self.assertContains(response, "period=all")
        self.assertEqual(report_data()["financial"], None)

    def test_all_time_exports_show_one_capital_total_after_recurring_costs(self):
        from reportlab.platypus import Table

        ProjectCost.objects.create(description="Solar panel item", amount=2029, date_added=self.now)
        ProjectCost.objects.create(description="Wire connector item", amount=69, date_added=self.now)
        OperatingExpense.objects.create(name="Globe at HOME 5G", amount=999, period="monthly", date_added=self.now)
        OperatingExpense.objects.create(name="Maintenance", amount=120, period="yearly", date_added=self.now)
        before_capital = list(ProjectCost.objects.order_by("pk").values())
        before_recurring = list(OperatingExpense.objects.order_by("pk").values())
        data = report_data("all")
        self.assertEqual(data["financial"]["capital"], 2098)
        self.assertEqual(data["financial"]["items"], [
            {"name": "Globe at HOME 5G", "frequency": "Monthly", "amount": 999},
            {"name": "Maintenance", "frequency": "Yearly", "amount": 120},
            {"name": "Total Capital Investment", "frequency": "One-time capital", "amount": 2098},
        ])
        self.assertEqual(data["financial"]["expenses"],
                         OperatingExpense.calculate_total_expenses(data["financial"]["days"]))
        self.assertEqual(data["financial"]["surplus"], round(data["revenue"] - data["financial"]["expenses"], 2))

        printed = self.client.get("/reports/generate/?period=all&format=print")
        self.assertContains(printed, "Total Capital Investment", count=1)
        self.assertContains(printed, "Globe at HOME 5G")
        self.assertContains(printed, "Maintenance")
        csv_response = report_csv(data)
        rows = self.rows(csv_response)
        capital_rows = [row for row in rows if row[0] == "Total Capital Investment"]
        self.assertEqual([row[:3] for row in capital_rows],
                         [["Total Capital Investment", "One-time capital", "2098.00"]])
        self.assertTrue(all(len(row) == 11 for row in rows))
        self.assertLess(next(i for i, row in enumerate(rows) if row[0] == "Globe at HOME 5G"),
                        next(i for i, row in enumerate(rows) if row[0] == "Total Capital Investment"))

        with patch("reports.web_exports.Table", wraps=Table) as tables:
            pdf = report_pdf(data)
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        pdf_tables = [[[cell.getPlainText() for cell in row] for row in call.args[0]]
                      for call in tables.call_args_list]
        costs = next(table for table in pdf_tables if table[0][0] == "Configured cost item")
        self.assertEqual(costs[1:], [
            ["Globe at HOME 5G", "Monthly", "999.00"],
            ["Maintenance", "Yearly", "120.00"],
            ["Total Capital Investment", "One-time capital", "2,098.00"],
        ])
        for label in ("Solar panel item", "Wire connector item"):
            self.assertNotContains(printed, label)
            self.assertNotIn(label, csv_response.content.decode("utf-8-sig"))
            self.assertNotIn(label, str(pdf_tables))
        self.assertEqual(before_capital, list(ProjectCost.objects.order_by("pk").values()))
        self.assertEqual(before_recurring, list(OperatingExpense.objects.order_by("pk").values()))

    def test_empty_capital_uses_zero_summary_without_creating_cost_records(self):
        data = report_data("all")
        self.assertEqual(data["financial"]["capital"], 0)
        self.assertIsNone(data["financial"]["ratio"])
        self.assertEqual(data["financial"]["items"], [
            {"name": "Total Capital Investment", "frequency": "One-time capital", "amount": 0},
        ])
        self.assertContains(self.client.get("/reports/generate/?period=all&format=print"),
                            "Total Capital Investment", count=1)
        self.assertIn(["Total Capital Investment", "One-time capital", "0.00"],
                      [row[:3] for row in self.rows(report_csv(data))])
        self.assertTrue(report_pdf(data).content.startswith(b"%PDF-"))
        self.assertFalse(ProjectCost.objects.exists())
        self.assertFalse(OperatingExpense.objects.exists())

    def test_empty_period_has_zero_totals_and_pdf_is_valid(self):
        data = report_data("custom", "2026-08-01", "2026-08-02")
        self.assertEqual(data["revenue"], 0)
        self.assertEqual(data["total_sessions"], 0)
        self.assertEqual(data["average_minutes"], 0)
        self.assertTrue(report_pdf(data).content.startswith(b"%PDF-"))

    def test_long_plan_names_and_markup_do_not_break_pdf_or_html(self):
        self.plan.name = '<script>alert("x")</script> & ' + "LongPlan" * 8
        self.plan.save(update_fields=["name"])
        self.assertTrue(report_pdf(report_data()).content.startswith(b"%PDF-"))
        response = self.client.get("/reports/generate/?format=print")
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")

    def test_pdf_embeds_unicode_font_for_peso_and_accented_labels(self):
        self.plan.name = "\u20b120 Plan - Ma\u00f1ana"
        self.plan.save(update_fields=["name"])
        content = report_pdf(report_data()).content
        self.assertIn(b"/FontFile2", content)
        self.assertIn(b"DejaVuSans", content)

    def test_outputs_have_private_headers(self):
        for path in ("/reports/generate/?format=print", "/reports/generate/?format=pdf",
                     "/reports/generate/?format=csv", "/iconnect-ops/revenue/export/",
                     "/iconnect-ops/sessions/export/?period=all"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "private, no-store")
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_non_staff_and_anonymous_cannot_read_any_report_export(self):
        guest = get_user_model().objects.create_user("report-guest", is_staff=False)
        for user in (None, guest):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for path in ("/reports/generate/?format=print", "/reports/generate/?format=pdf",
                         "/reports/generate/?format=csv", "/iconnect-ops/revenue/export/",
                         "/iconnect-ops/sessions/export/?period=all"):
                with self.subTest(user=user, path=path):
                    self.assertEqual(self.client.get(path).status_code, 302)

    def test_exports_do_not_change_customer_records(self):
        before = list(Session.objects.order_by("pk").values())
        for kind in ("print", "pdf", "csv"):
            self.client.get("/reports/generate/", {"period": "all", "format": kind})
        self.client.get("/iconnect-ops/revenue/export/")
        self.client.get("/iconnect-ops/sessions/export/?period=all")
        self.assertEqual(before, list(Session.objects.order_by("pk").values()))
        self.assertEqual(CoinEvent.objects.count(), 2)
        self.assertEqual(PurchaseTransaction.objects.count(), 1)

    def test_dashboard_print_and_sessions_links_use_explicit_all_time_scope(self):
        response = self.client.get("/iconnect-ops/reports/")
        self.assertContains(response, "type=all&amp;period=all&amp;format=print")
        self.assertContains(response, "/iconnect-ops/sessions/export/?period=all")
        self.assertContains(response, "Last 30 Days (Including Today)")
