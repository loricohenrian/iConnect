from django.contrib import admin
from .models import Plan, Session, CoinEvent, CoinInsertRequest, WhitelistedDevice, SuspiciousDevice


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'duration_minutes', 'speed_limit', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'mac_address', 'plan', 'status', 'amount_paid', 'time_in', 'time_out')
    list_filter = ('status', 'plan')
    search_fields = ('mac_address', 'device_name', 'ip_address', 'voucher_code')
    readonly_fields = ('created_at',)


@admin.register(CoinEvent)
class CoinEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'amount', 'denomination', 'session', 'timestamp')
    list_filter = ('denomination',)
    readonly_fields = ('timestamp',)


@admin.register(CoinInsertRequest)
class CoinInsertRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'mac_address',
        'purpose',
        'status',
        'expected_amount',
        'credited_amount',
        'created_at',
        'expires_at',
    )
    list_filter = ('purpose', 'status')
    search_fields = ('mac_address', 'ip_address')
    readonly_fields = ('created_at', 'activated_at', 'expires_at', 'completed_at')


@admin.register(WhitelistedDevice)
class WhitelistedDeviceAdmin(admin.ModelAdmin):
    list_display = ('device_name', 'mac_address', 'added_by', 'date_added')
    search_fields = ('device_name', 'mac_address')


@admin.register(SuspiciousDevice)
class SuspiciousDeviceAdmin(admin.ModelAdmin):
    list_display = (
        'mac_address',
        'last_ip_address',
        'reason',
        'status',
        'is_blocked',
        'detection_count',
        'last_detected_at',
    )
    list_filter = ('status', 'is_blocked', 'reason')
    search_fields = ('mac_address', 'last_ip_address', 'reason', 'evidence')
    readonly_fields = ('first_detected_at', 'last_detected_at', 'blocked_at', 'resolved_at')
