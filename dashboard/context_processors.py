from dashboard.models import SystemSettings, IssueReport
from sessions_app.models import Session, SuspiciousDevice

def system_settings_processor(request):
    try:
        settings_obj = SystemSettings.get_settings()
        new_alerts = SuspiciousDevice.objects.filter(status='new').count()
        pending_issues = IssueReport.objects.filter(status='pending').count()
        
        max_slots = settings_obj.max_concurrent_sessions
        active_count = Session.objects.filter(status='active').count()
        available_slots = max(0, max_slots - active_count)

        return {
            'sys_settings': settings_obj,
            'new_security_alerts_count': new_alerts,
            'pending_issues_count': pending_issues,
            'slots_active': active_count,
            'slots_max': max_slots,
            'slots_available': available_slots,
        }
    except Exception:
        return {}

