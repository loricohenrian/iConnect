from dashboard.models import SystemSettings, IssueReport
from sessions_app.models import SuspiciousDevice

def system_settings_processor(request):
    try:
        settings_obj = SystemSettings.get_settings()
        new_alerts = SuspiciousDevice.objects.filter(status='new').count()
        pending_issues = IssueReport.objects.filter(status='pending').count()
        return {
            'sys_settings': settings_obj,
            'new_security_alerts_count': new_alerts,
            'pending_issues_count': pending_issues,
        }
    except Exception:
        return {}

