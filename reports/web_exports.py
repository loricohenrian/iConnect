"""Read-only, consistently dated exports for the administrator report page.

Scheduled delivery keeps its existing generator; this module is web-only.
Coin collections and purchased-plan revenue are different ledgers, not additive.
"""
import csv
import io
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, Min, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from sessions_app.models import CoinEvent, PurchaseTransaction, Session


REPORT_TZ = ZoneInfo("Asia/Manila")
REPORT_NOTE = (
    "Gross coin collections include unassigned coins. Plan amounts are separate, "
    "not additional revenue. Today's totals are partial."
)


def csv_text(value):
    """Escape untrusted text without converting legitimate numeric cells to text."""
    text = str(value or "").replace("\x00", "")
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def private_response(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def report_data(period="today", start_date="", end_date=""):
    generated = timezone.localtime(timezone.now(), REPORT_TZ)
    today = generated.date()
    if period == "custom" or start_date or end_date:
        start = parse_date(start_date) if start_date else today
        end = parse_date(end_date) if end_date else today
        if start is None or end is None:
            raise ValueError("Enter valid dates in YYYY-MM-DD format.")
        start, end = sorted((start, end))
        label = "Custom date range"
    elif period == "all":
        first_dates = [model.objects.aggregate(first=Min(field))["first"]
                       for model, field in ((CoinEvent, "timestamp"),
                                            (PurchaseTransaction, "timestamp"), (Session, "time_in"))]
        start = min([timezone.localtime(stamp, REPORT_TZ).date()
                     for stamp in first_dates if stamp and stamp <= generated] + [today])
        end, label = today, "All recorded dates"
    else:
        days, label = {"today": (1, "Daily"), "week": (7, "Last 7 days"),
                       "month": (30, "Last 30 days")}.get(period, (1, "Daily"))
        start, end = today - timedelta(days=days - 1), today
    if (end.year, end.month, end.day) > (start.year + 10, start.month, start.day):
        raise ValueError("Choose a date range of 10 years or less.")

    lower = datetime.combine(start, time.min, tzinfo=REPORT_TZ)
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=REPORT_TZ)
    coins = CoinEvent.objects.filter(timestamp__gte=lower, timestamp__lt=upper, timestamp__lte=generated)
    purchases = PurchaseTransaction.objects.filter(timestamp__gte=lower, timestamp__lt=upper, timestamp__lte=generated)
    sessions = Session.objects.filter(time_in__gte=lower, time_in__lt=upper, time_in__lte=generated).select_related("plan").order_by("time_in", "id")
    coin_totals = coins.aggregate(revenue=Sum("amount"), events=Count("id"))
    session_totals = sessions.aggregate(count=Count("id"), minutes=Sum("duration_minutes_purchased"), average=Avg("duration_minutes_purchased"))
    purchase_totals = purchases.aggregate(revenue=Sum("amount"), count=Count("id"))

    def by_day(queryset, field, **aggregates):
        return {row["day"]: row for row in queryset.annotate(
            day=TruncDate(field, tzinfo=REPORT_TZ)).values("day").annotate(**aggregates).order_by("day")}

    coin_days = by_day(coins, "timestamp", revenue=Sum("amount"), events=Count("id"))
    session_days = by_day(sessions.order_by(), "time_in", sessions=Count("id"), minutes=Sum("duration_minutes_purchased"), average_minutes=Avg("duration_minutes_purchased"))
    peak_hours = {}
    for row in sessions.order_by().annotate(day=TruncDate("time_in", tzinfo=REPORT_TZ),
                                           hour=ExtractHour("time_in", tzinfo=REPORT_TZ)).values("day", "hour").annotate(count=Count("id")).order_by("day", "-count", "hour"):
        peak_hours.setdefault(row["day"], f'{row["hour"]:02d}:00')
    purchase_days = by_day(purchases, "timestamp", purchases=Count("id"), purchase_revenue=Sum("amount"))
    daily = []
    day = start
    while day <= end:
        values = {"date": day, "revenue": 0, "events": 0, "sessions": 0,
                  "minutes": 0, "purchases": 0, "purchase_revenue": 0,
                  "average_minutes": 0, "peak_hour": peak_hours.get(day, "")}
        for source in (coin_days, session_days, purchase_days):
            values.update({key: value for key, value in source.get(day, {}).items() if key != "day"})
        daily.append(values)
        day += timedelta(days=1)
    plans = list(purchases.values("plan__name", "plan__price").annotate(
        count=Count("id"), revenue=Sum("amount")).order_by("-count", "plan__name", "plan__price"))
    for plan in plans:
        plan["name"] = plan["plan__name"] or "Unspecified / deleted plan"
        plan["share"] = round(plan["revenue"] * 100 / purchase_totals["revenue"], 1) if purchase_totals["revenue"] else 0
    financial = None
    if period == "all" and not (start_date or end_date):
        from dashboard.models import OperatingExpense, ProjectCost
        first_cost = ProjectCost.objects.aggregate(first=Min("date_added"))["first"]
        cost_start = min(start, timezone.localtime(first_cost, REPORT_TZ).date()) if first_cost else start
        days_operating = max((today - cost_start).days + 1, 1)
        expenses = OperatingExpense.calculate_total_expenses(days_operating)
        capital = ProjectCost.total_cost()
        surplus = round((coin_totals["revenue"] or 0) - expenses, 2)
        financial = {"days": days_operating, "expenses": expenses, "capital": capital,
                     "surplus": surplus, "ratio": round(surplus / capital * 100, 1) if capital else None,
                     "items": [{"name": item.name, "frequency": item.get_period_display(), "amount": item.amount}
                               for item in OperatingExpense.objects.order_by("date_added", "pk")] +
                              [{"name": "Total Capital Investment", "frequency": "One-time capital", "amount": capital}]}
    return {
        "label": label, "start_date": start, "end_date": end, "financial": financial,
        "period_label": f"{start:%b %d, %Y} - {end:%b %d, %Y}",
        "generated_at": generated, "timezone_name": str(REPORT_TZ), "note": REPORT_NOTE,
        "revenue": coin_totals["revenue"] or 0, "coin_events": coin_totals["events"],
        "total_sessions": session_totals["count"], "purchased_minutes": session_totals["minutes"] or 0,
        "average_minutes": session_totals["average"] or 0,
        "purchase_revenue": purchase_totals["revenue"] or 0, "purchase_count": purchase_totals["count"],
        "daily": daily, "plans": plans, "sessions": sessions,
    }


def _csv_response(data, kind):
    response = private_response(HttpResponse(content_type="text/csv; charset=utf-8"))
    response["Content-Disposition"] = f'attachment; filename="iconnect_{kind}_{data["start_date"]:%Y%m%d}_{data["end_date"]:%Y%m%d}.csv"'
    response.write("\ufeff")
    return response


def daily_revenue_csv(data):
    """Flat, Excel-compatible daily collection ledger, including today's coins."""
    response = _csv_response(data, "daily_revenue")
    writer = csv.writer(response)
    writer.writerow(["Date (Asia/Manila)", "Day", "Coin Revenue (PHP)", "Coin Events", "Sessions Started", "Purchased Minutes", "Plan Purchase Transactions", "Plan Purchases (PHP)", "Avg Purchased Minutes/Session", "Peak Session-Start Hour (Asia/Manila)"])
    for row in data["daily"]:
        writer.writerow([row["date"].isoformat(), row["date"].strftime("%A"),
                         f'{row["revenue"]:.2f}', row["events"], row["sessions"], row["minutes"],
                         row["purchases"], f'{row["purchase_revenue"]:.2f}', f'{row["average_minutes"]:.2f}', row["peak_hour"]])
    writer.writerow(["TOTAL", "", f'{data["revenue"]:.2f}', data["coin_events"], data["total_sessions"], data["purchased_minutes"], data["purchase_count"], f'{data["purchase_revenue"]:.2f}', f'{data["average_minutes"]:.2f}', ""])
    return response


def report_csv(data):
    """Labeled report sections, uniformly eleven columns wide for spreadsheets."""
    response = _csv_response(data, "report")
    writer = csv.writer(response)

    def row(*cells):
        writer.writerow(list(cells) + [""] * (11 - len(cells)))

    row("iConnect Financial & Usage Report")
    row("Period", data["period_label"])
    row("Generated at (Asia/Manila)", data["generated_at"].strftime("%Y-%m-%d %H:%M:%S"))
    row("Currency", "PHP")
    row("Notes", data["note"])
    row()
    row("SUMMARY", "Value", "Unit")
    for name, value, unit in (("Coin revenue", f'{data["revenue"]:.2f}', "PHP"),
                              ("Sessions started", data["total_sessions"], "sessions"),
                              ("Purchased time", data["purchased_minutes"], "minutes"),
                              ("Average purchased duration", f'{data["average_minutes"]:.2f}', "minutes/session"),
                              ("Plan purchases (separate)", f'{data["purchase_revenue"]:.2f}', "PHP")):
        row(name, value, unit)
    if data["financial"]:
        financial = data["financial"]
        row()
        row("FINANCIAL SUMMARY", "Value", "Unit")
        row("Estimated expenses", f'{financial["expenses"]:.2f}', "PHP")
        row("Surplus before capital", f'{financial["surplus"]:.2f}', "PHP")
        row("Capital investment", f'{financial["capital"]:.2f}', "PHP")
        row("Surplus / capital", financial["ratio"] if financial["ratio"] is not None else "N/A", "%")
        row("Expense estimate period", financial["days"], "days")
        row("Expense basis", "Prorated recurring costs, not paid bills. Surplus excludes capital.")
        row("Cost item", "Frequency", "Rate / capital (PHP)")
        for item in financial["items"]:
            row(csv_text(item["name"]), item["frequency"], f'{item["amount"]:.2f}')
    row()
    row("DAILY BREAKDOWN")
    row("Date (Asia/Manila)", "Day", "Coin Revenue (PHP)", "Coin Events", "Sessions Started", "Purchased Minutes", "Plan Purchase Transactions", "Plan Purchases (PHP)", "Avg Purchased Minutes/Session", "Peak Session-Start Hour (Asia/Manila)")
    for item in data["daily"]:
        row(item["date"].isoformat(), item["date"].strftime("%A"), f'{item["revenue"]:.2f}', item["events"], item["sessions"], item["minutes"], item["purchases"], f'{item["purchase_revenue"]:.2f}', f'{item["average_minutes"]:.2f}', item["peak_hour"])
    row("TOTAL", "", f'{data["revenue"]:.2f}', data["coin_events"], data["total_sessions"], data["purchased_minutes"], data["purchase_count"], f'{data["purchase_revenue"]:.2f}', f'{data["average_minutes"]:.2f}')
    row()
    row("PLAN PURCHASES")
    row("Plan Name", "Current Rate (PHP)", "Purchase Transactions", "Purchased Amount (PHP)", "Share of Plan Purchases (%)")
    for plan in data["plans"]:
        row(csv_text(plan["name"]), plan["plan__price"] if plan["plan__price"] is not None else "", plan["count"], f'{plan["revenue"]:.2f}', f'{plan["share"]:.1f}')
    row("TOTAL", "", data["purchase_count"], f'{data["purchase_revenue"]:.2f}', "100.0" if data["purchase_count"] else "0.0")
    row()
    row("SESSION DETAILS - private device data")
    row("Session ID", "MAC Address", "IP Address", "Device Name", "Current Plan", "Session Amount Recorded (PHP)", "Duration Purchased (min)", "Current Status", "Time In (Asia/Manila)", "Time Out (Asia/Manila)", "Saved Data Usage (MiB)")
    row("Note", "Lifetime session amounts; not period collections.")
    for session in data["sessions"].iterator():
        row(session.pk, csv_text(session.mac_address), csv_text(session.ip_address), csv_text(session.device_name),
            csv_text(session.plan.name if session.plan else "Unspecified / deleted plan"), f"{session.amount_paid:.2f}",
            session.duration_minutes_purchased, csv_text(session.get_status_display()),
            timezone.localtime(session.time_in, REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S") if session.time_in else "",
            timezone.localtime(session.time_out, REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S") if session.time_out else "",
            f"{session.bandwidth_used_mb or 0:.2f}")
    return response


def report_print(request, data):
    params = {"period": "custom", "type": "custom", "start_date": data["start_date"].isoformat(), "end_date": data["end_date"].isoformat()}
    if data["financial"]:
        params = {"period": "all", "type": "all"}
    base = reverse("reports:generate")
    context = {**data, "pdf_url": base + "?" + urlencode({**params, "format": "pdf"}),
               "csv_url": base + "?" + urlencode({**params, "format": "csv"})}
    return private_response(render(request, "reports/print.html", context))


def report_pdf(data):
    """A4 summary with wrapped cells, repeated table headers and page numbers."""
    buffer = io.BytesIO()
    font_name = "IConnectReport"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(Path(__file__).parent / "fonts" / "DejaVuSans.ttf")))
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=18*mm, title="iConnect Financial & Usage Report", author="iConnect")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("ReportTitle", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=colors.HexColor("#7b2d3b"), spaceAfter=9))
    styles.add(ParagraphStyle("ReportCell", fontName=font_name, fontSize=9, leading=12))
    styles.add(ParagraphStyle("ReportNote", fontName=font_name, fontSize=9, leading=12, textColor=colors.HexColor("#475569"), spaceAfter=8))
    styles.add(ParagraphStyle("TableNote", parent=styles["ReportNote"], keepWithNext=True))
    styles["Heading2"].keepWithNext = True
    styles.add(ParagraphStyle("DailyHeading", parent=styles["Heading2"], keepWithNext=False))
    story = []

    def text(value, style="ReportCell"):
        return Paragraph(escape(str(value)), styles[style])

    def table(rows, widths):
        result = Table([[text(value, "ReportCell" if index == 0 else "ReportNumber")
                         for index, value in enumerate(row)] for row in rows], colWidths=[doc.width * value for value in widths], repeatRows=1, hAlign="LEFT")
        result.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#7b2d3b")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        if len(rows) > 2 and rows[-1][0] == "TOTAL":
            # A totals-only continuation page is not useful to a reader.
            result.setStyle(TableStyle([("NOSPLIT", (0, -2), (-1, -1))]))
        return result

    styles.add(ParagraphStyle("ReportNumber", parent=styles["ReportCell"], alignment=2))
    story += [text("iConnect", "ReportTitle"), text("Financial & Usage Report", "Heading2"),
              text(f'{data["label"]} | {data["period_label"]}', "ReportNote"),
              text(f'Generated: {data["generated_at"]:%Y-%m-%d %H:%M:%S} Asia/Manila | Currency: PHP', "ReportNote")]
    story.append(table([["Coin revenue (PHP)", "Sessions started", "Avg purchased minutes/session"],
                        [f'{data["revenue"]:,.2f}', data["total_sessions"], f'{data["average_minutes"]:,.2f}']], [.34, .30, .36]))
    story += [Spacer(1, 10), text(data["note"], "ReportNote")]
    if data["financial"]:
        financial = data["financial"]
        story += [text("Financial summary", "Heading2"),
                  table([["Measure", "Value"],
                         ["Estimated expenses (PHP)", f'{financial["expenses"]:,.2f}'],
                         ["Surplus before capital (PHP)", f'{financial["surplus"]:,.2f}'],
                         ["Capital investment (PHP)", f'{financial["capital"]:,.2f}'],
                         ["Surplus / capital (%)", f'{financial["ratio"]:.1f}' if financial["ratio"] is not None else "N/A"]], [.70, .30]),
                  Spacer(1, 8), text(f'Estimated recurring costs over {financial["days"]} days; not paid bills. Surplus excludes capital.', "ReportNote")]
        if financial["items"]:
            story.append(table([["Cost item", "Frequency", "Rate / capital (PHP)"]] +
                               [[item["name"], item["frequency"], f'{item["amount"]:,.2f}'] for item in financial["items"]], [.52, .26, .22]))
    # Reserve room for the heading, header and first row, not the entire ledger.
    story += [CondPageBreak(100), text("Daily summary", "DailyHeading")]
    rows = [["Date / day", "Coin revenue (PHP)", "Coin events", "Sessions started", "Purchased minutes", "Plan purchases (PHP)"]]
    rows += [[f'{row["date"]:%Y-%m-%d} {row["date"]:%a}', f'{row["revenue"]:,.2f}', row["events"], row["sessions"], row["minutes"], f'{row["purchase_revenue"]:,.2f}'] for row in data["daily"]]
    rows.append(["TOTAL", f'{data["revenue"]:,.2f}', data["coin_events"], data["total_sessions"], data["purchased_minutes"], f'{data["purchase_revenue"]:,.2f}'])
    story.append(table(rows, [.24, .17, .12, .13, .17, .17]))
    story += [Spacer(1, 8), text("Plan purchases", "Heading2"), text("Counts are transactions; rates are current prices.", "TableNote")]
    rows = [["Plan", "Current rate (PHP)", "Transactions", "Amount (PHP)", "Share (%)"]]
    rows += [[row["name"], row["plan__price"] if row["plan__price"] is not None else "N/A", row["count"], f'{row["revenue"]:,.2f}', f'{row["share"]:.1f}'] for row in data["plans"]]
    if not data["plans"]:
        rows.append(["No plan purchases recorded", "", "0", "0.00", "0.0"])
    rows.append(["TOTAL", "", data["purchase_count"], f'{data["purchase_revenue"]:,.2f}', "100.0" if data["purchase_count"] else "0.0"])
    story.append(table(rows, [.40, .16, .14, .18, .12]))
    story += [Spacer(1, 10), text("System-generated, unaudited report.", "ReportNote")]

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(doc.leftMargin, 10*mm, f'iConnect | {data["start_date"]} - {data["end_date"]} | PHP')
        canvas.drawRightString(A4[0] - doc.rightMargin, 10*mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    response = private_response(HttpResponse(buffer.getvalue(), content_type="application/pdf"))
    response["Content-Disposition"] = f'attachment; filename="iconnect_report_{data["start_date"]:%Y%m%d}_{data["end_date"]:%Y%m%d}.pdf"'
    return response
