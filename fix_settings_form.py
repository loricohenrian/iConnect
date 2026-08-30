import re

with open("dashboard/templates/dashboard/settings.html", "r", encoding="utf-8") as f:
    html = f.read()

html = html.replace('<form method="POST" action="">', '')
html = html.replace('{% csrf_token %}', '')

html = html.replace('<div class="grid-2">', '<form method="POST" action="">\n{% csrf_token %}\n<div class="grid-2">', 1)

html = html.replace('</form>', '')

# Replace only the LAST {% endblock %}
parts = html.rsplit('{% endblock %}', 1)
html = '</form>\n{% endblock %}'.join(parts)

html = html.replace('Save All Settings', 'Save Settings')
html = html.replace('Save Gamification Settings', 'Save Settings')
html = html.replace('Save Family Pass Settings', 'Save Settings')

with open("dashboard/templates/dashboard/settings.html", "w", encoding="utf-8") as f:
    f.write(html)

print("HTML fixed properly.")
