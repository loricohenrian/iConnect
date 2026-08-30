import os

def update_css(file_path, prefix):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Update wrap background
    wrap_class = f".{prefix}-wrap {{"
    old_bg_line = "background: #f8fafc;"
    new_bg_line = "background: #8B0000;"
    
    if old_bg_line in content:
        content = content.replace(old_bg_line, new_bg_line)

    # Update logo to be circular
    logo_class = f".{prefix}-logo {{"
    if logo_class in content:
        # We find the closing brace of the logo block
        start_idx = content.find(logo_class)
        end_idx = content.find("}", start_idx)
        
        logo_block = content[start_idx:end_idx]
        if "border-radius" not in logo_block:
            new_logo_block = logo_block + "    border-radius: 50%;\n        object-fit: cover;\n    "
            content = content.replace(logo_block, new_logo_block)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


# 1. Update login.html
login_path = r'c:\Users\Henrian\Desktop\iConnect\dashboard\templates\dashboard\login.html'
update_css(login_path, 'login')

# 2. Update all auth templates
auth_dir = r'c:\Users\Henrian\Desktop\iConnect\dashboard\templates\dashboard\auth'
for filename in os.listdir(auth_dir):
    if filename.endswith('.html'):
        update_css(os.path.join(auth_dir, filename), 'auth')

print("Updated background colors and logo border-radius.")
