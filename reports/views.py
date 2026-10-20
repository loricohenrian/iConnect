"""
Reports Views
"""
import logging

from django.http import HttpResponse
from django.contrib.auth.decorators import user_passes_test
from .web_exports import private_response, report_csv, report_data, report_pdf, report_print


logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('audit')


def _is_dashboard_admin(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def generate_report_view(request):
    """Generate and download a PDF or CSV report."""
    report_type = request.GET.get('type', 'daily')
    period = request.GET.get('period') or {'weekly': 'week', 'monthly': 'month', 'all': 'all'}.get(report_type, 'today')
    output_format = request.GET.get('format', 'pdf').lower()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    if start_date or end_date or period == 'custom':
        period = 'custom'
        report_type = 'custom'
    elif report_type not in ('daily', 'weekly', 'monthly', 'all'):
        report_type = 'daily'

    if period not in ('today', 'week', 'month', 'custom', 'all'):
        period = 'today'

    if output_format not in ('pdf', 'csv', 'print'):
        output_format = 'pdf'

    try:
        data = report_data(period, start_date, end_date)
    except (ValueError, OverflowError):
        return private_response(HttpResponse('Choose valid report dates in YYYY-MM-DD format, no more than 10 years apart.', status=400))
    audit_logger.info(
        'event=report_generate user=%s report_type=%s period=%s format=%s start_date=%s end_date=%s',
        request.user.username, report_type, period, output_format,
        data['start_date'].isoformat(), data['end_date'].isoformat(),
    )
    if output_format == 'print':
        return report_print(request, data)
    if output_format == 'csv':
        return report_csv(data)
    return report_pdf(data)
