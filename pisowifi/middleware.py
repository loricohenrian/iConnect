import zoneinfo
from django.conf import settings
from django.utils import timezone


class TimezoneMiddleware:
    """
    Middleware that ensures every HTTP request executes within the configured
    local timezone (default: Asia/Manila, GMT+8).

    Guards against host OS, Armbian, Docker, or environment variables where
    TIMEZONE or TZ might default to UTC, guaranteeing all rendered template
    filters (date) and views display Philippine local time correctly.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        tz_name = getattr(settings, 'TIME_ZONE', 'Asia/Manila')
        try:
            self.tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            self.tz = zoneinfo.ZoneInfo('Asia/Manila')

    def __call__(self, request):
        timezone.activate(self.tz)
        return self.get_response(request)
