import re

with open("dashboard/templates/dashboard/settings.html", "r", encoding="utf-8") as f:
    code = f.read()

# Fix the page_title block
code = re.sub(
    r'\{% block page_title %\}Global Settings.*?(?=\{\% endblock \%\})\{\% endblock \%\}',
    '{% block page_title %}Global Settings{% endblock %}',
    code,
    flags=re.DOTALL
)

# Fix the topbar_title block
code = re.sub(
    r'\{% block topbar_title %\}Global Settings.*?(?=\{\% endblock \%\})\{\% endblock \%\}',
    '{% block topbar_title %}Global Settings{% endblock %}',
    code,
    flags=re.DOTALL
)

with open("dashboard/templates/dashboard/settings.html", "w", encoding="utf-8") as f:
    f.write(code)

print("settings.html patched.")
