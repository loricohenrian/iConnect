import os

views_path = r"c:\Users\Henrian\Desktop\iConnect\sessions_app\views.py"

with open(views_path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    "from .models import Session, CoinEvent, CoinInsertRequest",
    "from .models import Session, CoinEvent, CoinInsertRequest, SessionGroup"
)

with open(views_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patched!")
