import os

urls_path = r'c:\Users\Henrian\Desktop\iConnect\dashboard\urls_dashboard.py'

with open(urls_path, 'r', encoding='utf-8') as f:
    content = f.read()

if "auth_views" not in content:
    content = content.replace("from django.urls import path", "from django.urls import path\nfrom django.contrib.auth import views as auth_views")
    
    new_urls = """    path('login/', views.dashboard_login, name='login'),
    
    # Password Reset
    path('password_reset/', auth_views.PasswordResetView.as_view(
        template_name='dashboard/auth/password_reset_form.html',
        email_template_name='dashboard/auth/password_reset_email.html',
        success_url='/dashboard/password_reset/done/'
    ), name='password_reset'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='dashboard/auth/password_reset_done.html'
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='dashboard/auth/password_reset_confirm.html',
        success_url='/dashboard/reset/done/'
    ), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='dashboard/auth/password_reset_complete.html'
    ), name='password_reset_complete'),"""
    
    content = content.replace("    path('login/', views.dashboard_login, name='login'),", new_urls)
    
    with open(urls_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched dashboard urls.")
