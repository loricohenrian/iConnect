import re

with open("sessions_app/serializers.py", "r", encoding="utf-8") as f:
    code = f.read()

# Replace SessionSerializer fields and methods
code = re.sub(
    r"class SessionSerializer\(serializers\.ModelSerializer\):\n.*?class Meta:",
    r"""class SessionSerializer(serializers.ModelSerializer):
    plan_name = serializers.SerializerMethodField()
    remaining_minutes = serializers.SerializerMethodField()
    time_remaining_display = serializers.ReadOnlyField()
    time_remaining_seconds = serializers.ReadOnlyField()

    def get_plan_name(self, obj):
        if obj.plan:
            return obj.plan.name
        if getattr(obj, 'session_group', None):
            return f"Family Pass ({obj.session_group.group_code})"
        return "Unknown"

    def get_remaining_minutes(self, obj):
        return round(obj.time_remaining_seconds / 60, 2)

    class Meta:""",
    code,
    flags=re.DOTALL
)

# Replace SessionStartSerializer
code = re.sub(
    r"class SessionStartSerializer\(serializers\.Serializer\):\n.*?def validate_mac_address\(self, value\):\n\s*return normalize_mac_address\(value\)\n",
    r"""class SessionStartSerializer(serializers.Serializer):
    mac_address = serializers.CharField(max_length=17)
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    ip_address = serializers.IPAddressField(required=False)
    device_name = serializers.CharField(max_length=100, required=False)
    
    is_group_pass = serializers.BooleanField(default=False)
    group_pass_devices = serializers.IntegerField(required=False, allow_null=True)
    group_pass_duration_minutes = serializers.IntegerField(required=False, allow_null=True)

    def validate_mac_address(self, value):
        return normalize_mac_address(value)

    def validate(self, data):
        is_group_pass = data.get('is_group_pass', False)
        if is_group_pass:
            if not data.get('group_pass_devices'):
                raise serializers.ValidationError("group_pass_devices is required for family pass.")
            if not data.get('group_pass_duration_minutes'):
                raise serializers.ValidationError("group_pass_duration_minutes is required for family pass.")
        else:
            if not data.get('plan_id'):
                raise serializers.ValidationError("plan_id is required for standard sessions.")
        return data
""",
    code,
    flags=re.DOTALL
)

# Add GroupJoinSerializer at the end
code += """

class GroupJoinSerializer(serializers.Serializer):
    group_code = serializers.CharField(max_length=10)
    mac_address = serializers.CharField(max_length=17)
    ip_address = serializers.IPAddressField(required=False)
    device_name = serializers.CharField(max_length=100, required=False)

    def validate_mac_address(self, value):
        return normalize_mac_address(value)
"""

with open("sessions_app/serializers.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Patched serializers.py successfully!")
