from dashboard.models import SystemSettings

def system_settings_processor(request):
    try:
        return {'sys_settings': SystemSettings.get_settings()}
    except Exception:
        return {}
