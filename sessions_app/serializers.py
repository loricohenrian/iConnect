"""
Serializers for Session Management API
"""
import re
from rest_framework import serializers
from .models import Plan, Session, CoinEvent, WhitelistedDevice


MAC_ADDRESS_RE = re.compile(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$')


def normalize_mac_address(value):
    mac_address = value.upper()
    if not MAC_ADDRESS_RE.match(mac_address):
        raise serializers.ValidationError(
            'MAC address must use the format AA:BB:CC:DD:EE:FF'
        )
    return mac_address


class PlanSerializer(serializers.ModelSerializer):
    duration_display = serializers.ReadOnlyField()
    price_per_minute = serializers.ReadOnlyField()

    class Meta:
        model = Plan
        fields = [
            'id', 'name', 'price', 'duration_minutes',
            'duration_display', 'price_per_minute', 'speed_limit', 'is_active', 'created_at'
        ]


class SessionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source='plan.name', read_only=True)
    remaining_minutes = serializers.SerializerMethodField()
    time_remaining_display = serializers.ReadOnlyField()
    time_remaining_seconds = serializers.ReadOnlyField()

    def get_remaining_minutes(self, obj):
        return round(obj.time_remaining_seconds / 60, 2)

    class Meta:
        model = Session
        fields = [
            'id', 'mac_address', 'plan', 'plan_name',
            'time_in', 'time_out', 'duration_minutes_purchased',
            'remaining_minutes', 'amount_paid', 'status',
            'voucher_code', 'bandwidth_used_mb', 'ip_address',
            'device_name', 'time_remaining_display',
            'time_remaining_seconds', 'created_at'
        ]


class CoinEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoinEvent
        fields = ['id', 'amount', 'denomination', 'mac_address', 'session', 'timestamp']


class CoinInsertedSerializer(serializers.Serializer):
    """Serializer for the coin-inserted API endpoint."""
    amount = serializers.IntegerField(min_value=1, max_value=20)
    denomination = serializers.IntegerField()
    mac_address = serializers.CharField(max_length=17, required=False)

    def validate_mac_address(self, value):
        return normalize_mac_address(value)

    def validate_denomination(self, value):
        valid = [1, 5, 10, 20]
        if value not in valid:
            raise serializers.ValidationError(
                f'Invalid denomination. Must be one of: {valid}'
            )
        return value

    def validate_amount(self, value):
        if value not in [1, 5, 10, 20]:
            raise serializers.ValidationError(
                'Amount must match a valid coin denomination (₱1, ₱5, ₱10, ₱20)'
            )
        return value

    def validate(self, attrs):
        amount = attrs.get('amount')
        denomination = attrs.get('denomination')
        if amount is not None and denomination is not None and amount != denomination:
            raise serializers.ValidationError(
                {'amount': 'Amount must exactly match denomination for a single coin event.'}
            )
        return attrs


class SessionStartSerializer(serializers.Serializer):
    """Serializer for starting a new session."""
    mac_address = serializers.CharField(max_length=17)
    plan_id = serializers.IntegerField()
    ip_address = serializers.IPAddressField(required=False)
    device_name = serializers.CharField(max_length=100, required=False)

    def validate_mac_address(self, value):
        return normalize_mac_address(value)


class SessionExtendSerializer(serializers.Serializer):
    """Serializer for extending a session with a voucher code."""
    voucher_code = serializers.CharField(max_length=10)
    mac_address = serializers.CharField(max_length=17)

    def validate_mac_address(self, value):
        return normalize_mac_address(value)


class WhitelistedDeviceSerializer(serializers.ModelSerializer):
    def validate_mac_address(self, value):
        return normalize_mac_address(value)

    class Meta:
        model = WhitelistedDevice
        fields = ['id', 'mac_address', 'device_name', 'added_by', 'date_added']
