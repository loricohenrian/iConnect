import re

with open("sessions_app/serializers.py", "r", encoding="utf-8") as f:
    code = f.read()

old_validate = """    def validate(self, data):
        is_group_pass = data.get('is_group_pass', False)
        if is_group_pass:
            if not data.get('group_pass_devices'):
                raise serializers.ValidationError("group_pass_devices is required for family pass.")
            if not data.get('group_pass_duration_minutes'):
                raise serializers.ValidationError("group_pass_duration_minutes is required for family pass.")
        else:
            if not data.get('plan_id'):
                raise serializers.ValidationError("plan_id is required for standard sessions.")
        return data"""

new_validate = """    def validate(self, data):
        is_group_pass = data.get('is_group_pass', False)
        if is_group_pass:
            if not data.get('group_pass_devices'):
                raise serializers.ValidationError("group_pass_devices is required for group plan.")
        
        if not data.get('plan_id'):
            raise serializers.ValidationError("plan_id is required.")
        return data"""

code = code.replace(old_validate, new_validate)

with open("sessions_app/serializers.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/serializers.py patched.")
