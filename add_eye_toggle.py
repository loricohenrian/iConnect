import os

login_path = r'c:\Users\Henrian\Desktop\iConnect\dashboard\templates\dashboard\login.html'

with open(login_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Add CSS for the eye toggle
css_injection = """    .password-wrapper {
        position: relative;
        display: flex;
        align-items: center;
    }

    .password-wrapper input {
        padding-right: 40px;
    }

    .toggle-password {
        position: absolute;
        right: 12px;
        background: none;
        border: none;
        cursor: pointer;
        padding: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #64748b;
    }
    
    .toggle-password:hover {
        color: #0f172a;
    }

    .forgot-link {"""

if ".password-wrapper" not in content:
    content = content.replace("    .forgot-link {", css_injection)

# Modify the password input HTML
old_html = """            <div class="input-group">
                <label for="password">Password</label>
                <input id="password" type="password" name="password" placeholder="••••••••" required>
            </div>"""

new_html = """            <div class="input-group">
                <label for="password">Password</label>
                <div class="password-wrapper">
                    <input id="password" type="password" name="password" placeholder="••••••••" required>
                    <button type="button" class="toggle-password" id="togglePassword" aria-label="Toggle password visibility">
                        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="feather feather-eye"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                    </button>
                </div>
            </div>"""

if old_html in content:
    content = content.replace(old_html, new_html)

# Add the script at the bottom of the body
script_html = """
<script>
    const togglePassword = document.querySelector('#togglePassword');
    const password = document.querySelector('#password');
    const eyeIcon = togglePassword.querySelector('svg');

    togglePassword.addEventListener('click', function (e) {
        const type = password.getAttribute('type') === 'password' ? 'text' : 'password';
        password.setAttribute('type', type);
        
        if (type === 'text') {
            // Eye off icon
            eyeIcon.innerHTML = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line>';
        } else {
            // Eye on icon
            eyeIcon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>';
        }
    });
</script>
{% endblock %}"""

if "const togglePassword" not in content:
    content = content.replace("{% endblock %}", script_html)

with open(login_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Added password toggle.")
