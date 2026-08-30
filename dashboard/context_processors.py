from dashboard.models import SystemSettings
from sessions_app.models import SuspiciousDevice

def system_settings_processor(request):
    try:
        settings_obj = SystemSettings.get_settings()
        new_alerts = SuspiciousDevice.objects.filter(status='new').count()
        return {
            'sys_settings': settings_obj,
            'new_security_alerts_count': new_alerts,
        }
    except Exception:
        return {}
