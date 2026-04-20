"""
Reports Views
"""
import logging

from django.http import HttpResponse
from django.contrib.auth.decorators import user_passes_test
from .generator import generate_report, generate_csv_report


logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('audit')


def _is_dashboard_admin(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def generate_report_view(request):
    """Generate and download a PDF or CSV report."""
    report_type = request.GET.get('type', 'daily')
    period = request.GET.get('period', 'today')
    output_format = request.GET.get('format', 'pdf').lower()

    if report_type not in ('daily', 'weekly', 'monthly'):
        report_type = 'daily'
    if period not in ('today', 'week', 'month'):
        period = 'today'

    if output_format not in ('pdf', 'csv'):
        output_format = 'pdf'

    audit_logger.info(
        'event=report_generate user=%s report_type=%s period=%s format=%s',
        request.user.username,
        report_type,
        period,
        output_format,
    )

    if output_format == 'csv':
        return generate_csv_report(report_type, period)

    return generate_report(report_type, period)
