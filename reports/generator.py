"""
iConnect — PDF Report Generator using ReportLab
"""
import io
import csv
from datetime import timedelta, date
from django.utils import timezone
from django.db.models import Sum, Count, Avg
from django.http import HttpResponse

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from sessions_app.models import Session, Plan
from dashboard.models import DailyRevenueSummary, ProjectCost


# iConnect brand colors (reportlab format)
PISONET_BLUE = colors.HexColor('#1A73E8')
PISONET_DARK = colors.HexColor('#1E293B')
PISONET_LIGHT = colors.HexColor('#F1F5F9')
PISONET_BORDER = colors.HexColor('#E2E8F0')
PISONET_SUCCESS = colors.HexColor('#10B981')
PISONET_GRAY = colors.HexColor('#64748B')


def get_styles():
    """Custom styles for iConnect reports."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='PisoTitle',
        fontName='Helvetica-Bold',
        fontSize=22,
        textColor=PISONET_DARK,
        spaceAfter=6,
    ))

    styles.add(ParagraphStyle(
        name='PisoSubtitle',
        fontName='Helvetica',
        fontSize=12,
        textColor=PISONET_GRAY,
        spaceAfter=20,
    ))

    styles.add(ParagraphStyle(
        name='PisoHeading',
        fontName='Helvetica-Bold',
        fontSize=14,
        textColor=PISONET_BLUE,
        spaceBefore=16,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name='PisoBody',
        fontName='Helvetica',
        fontSize=10,
        textColor=PISONET_DARK,
        spaceAfter=6,
    ))

    styles.add(ParagraphStyle(
        name='PisoStat',
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=PISONET_BLUE,
        alignment=TA_CENTER,
    ))

    return styles


def resolve_period(report_type='daily', period='today'):
    """Resolve date window and label for report generation."""
    today = timezone.now().date()

    if period == 'today' or report_type == 'daily':
        start_date = today
        period_label = today.strftime('%B %d, %Y')
    elif period == 'week' or report_type == 'weekly':
        start_date = today - timedelta(days=7)
        period_label = f'{start_date.strftime("%b %d")} — {today.strftime("%b %d, %Y")}'
    else:
        start_date = today - timedelta(days=30)
        period_label = f'{start_date.strftime("%b %d")} — {today.strftime("%b %d, %Y")}'

    return start_date, period_label


def get_report_data(report_type='daily', period='today'):
    """Collect reusable report aggregates and row-level data."""
    start_date, period_label = resolve_period(report_type, period)
    sessions = Session.objects.filter(
        time_in__date__gte=start_date,
        status__in=['active', 'expired']
    ).select_related('plan').order_by('-time_in')

    total_revenue = sessions.aggregate(total=Sum('amount_paid'))['total'] or 0
    total_sessions = sessions.count()
    avg_duration = sessions.aggregate(avg=Avg('duration_minutes_purchased'))['avg'] or 0

    plan_stats = sessions.values('plan__name', 'plan__price').annotate(
        count=Count('id'),
        revenue=Sum('amount_paid')
    ).order_by('-count')

    return {
        'sessions': sessions,
        'total_revenue': total_revenue,
        'total_sessions': total_sessions,
        'avg_duration': avg_duration,
        'plan_stats': plan_stats,
        'period_label': period_label,
    }


def generate_report(report_type='daily', period='today'):
    """
    Generate a PDF report.

    Args:
        report_type: 'daily', 'weekly', or 'monthly'
        period: 'today', 'week', or 'month'

    Returns:
        HttpResponse with PDF content
    """
    data = get_report_data(report_type, period)
    period_label = data['period_label']
    total_revenue = data['total_revenue']
    total_sessions = data['total_sessions']
    avg_duration = data['avg_duration']
    plan_stats = data['plan_stats']

    # Build PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1*cm, bottomMargin=1*cm)
    styles = get_styles()
    elements = []

    # Header
    elements.append(Paragraph('iConnect', styles['PisoTitle']))
    elements.append(Paragraph(f'{report_type.title()} Report — {period_label}', styles['PisoSubtitle']))
    elements.append(HRFlowable(width='100%', thickness=2, color=PISONET_BLUE))
    elements.append(Spacer(1, 12))

    # Summary Stats
    elements.append(Paragraph('Summary', styles['PisoHeading']))

    stats_data = [
        ['Total Revenue', 'Total Sessions', 'Avg Duration'],
        [f'₱{total_revenue:,}', str(total_sessions), f'{avg_duration:.0f} min'],
    ]

    stats_table = Table(stats_data, colWidths=[5*cm, 5*cm, 5*cm])
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PISONET_LIGHT),
        ('TEXTCOLOR', (0, 0), (-1, 0), PISONET_GRAY),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, 1), 16),
        ('TEXTCOLOR', (0, 1), (-1, 1), PISONET_BLUE),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 0.5, PISONET_BORDER),
        ('BOX', (0, 0), (-1, -1), 1, PISONET_BORDER),
    ]))
    elements.append(stats_table)
    elements.append(Spacer(1, 16))

    # Plan Breakdown
    if plan_stats:
        elements.append(Paragraph('Revenue by Plan', styles['PisoHeading']))

        plan_data = [['Plan', 'Sessions', 'Revenue']]
        for p in plan_stats:
            plan_data.append([
                p['plan__name'],
                str(p['count']),
                f'₱{p["revenue"]:,}',
            ])

        plan_table = Table(plan_data, colWidths=[7*cm, 4*cm, 4*cm])
        plan_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PISONET_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, PISONET_BORDER),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PISONET_LIGHT]),
        ]))
        elements.append(plan_table)
        elements.append(Spacer(1, 16))

    # Footer
    elements.append(Spacer(1, 24))
    elements.append(HRFlowable(width='100%', thickness=1, color=PISONET_BORDER))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        f'Generated on {timezone.now().strftime("%B %d, %Y at %I:%M %p")} — iConnect System',
        ParagraphStyle('Footer', fontName='Helvetica', fontSize=8, textColor=PISONET_GRAY, alignment=TA_CENTER)
    ))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="iconnect_{report_type}_report.pdf"'
    return response


def generate_csv_report(report_type='daily', period='today'):
    """Generate a CSV report with summary, plan breakdown, and session rows."""
    data = get_report_data(report_type, period)
    sessions = data['sessions']

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="iconnect_{report_type}_report.csv"'

    writer = csv.writer(response)
    writer.writerow(['iConnect Report'])
    writer.writerow(['Type', report_type.title()])
    writer.writerow(['Period', data['period_label']])
    writer.writerow([])

    writer.writerow(['Summary'])
    writer.writerow(['Total Revenue', f"{data['total_revenue']}"])
    writer.writerow(['Total Sessions', f"{data['total_sessions']}"])
    writer.writerow(['Average Duration (min)', f"{data['avg_duration']:.2f}"])
    writer.writerow([])

    writer.writerow(['Plan Breakdown'])
    writer.writerow(['Plan Name', 'Price', 'Sessions', 'Revenue'])
    for row in data['plan_stats']:
        writer.writerow([
            row['plan__name'],
            row['plan__price'],
            row['count'],
            row['revenue'],
        ])
    writer.writerow([])

    writer.writerow(['Sessions'])
    writer.writerow([
        'Session ID', 'MAC Address', 'Plan', 'Amount Paid',
        'Duration Purchased', 'Status', 'Time In', 'Time Out', 'Bandwidth MB'
    ])
    for session in sessions:
        writer.writerow([
            session.id,
            session.mac_address,
            session.plan.name,
            session.amount_paid,
            session.duration_minutes_purchased,
            session.status,
            session.time_in.strftime('%Y-%m-%d %H:%M:%S') if session.time_in else '',
            session.time_out.strftime('%Y-%m-%d %H:%M:%S') if session.time_out else '',
            session.bandwidth_used_mb,
        ])

    return response



