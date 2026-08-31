"""
Dashboard Template View URLs
"""
from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('login/', views.dashboard_login, name='login'),
    
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
    ), name='password_reset_complete'),
    path('logout/', views.dashboard_logout, name='logout'),
    path('', views.overview, name='overview'),
    path('revenue/', views.revenue, name='revenue'),
    path('sessions/', views.sessions_view, name='sessions'),
    path('sessions/export/', views.export_sessions_csv, name='export_sessions_csv'),
    path('revenue/export/', views.export_revenue_csv, name='export_revenue_csv'),
    path('reports/', views.reports, name='reports'),
    path('heatmap/', views.heatmap, name='heatmap'),
    path('analytics/', views.analytics_view, name='analytics'),
    path('roi/', views.roi, name='roi'),
    path('announcements/', views.announcements_view, name='announcements'),
    path('plans/', views.plans_view, name='plans'),
    path('settings/', views.settings_view, name='settings'),
    path('gamification/', views.gamification_view, name='gamification'),
    path('gamification/prize/<int:prize_id>/delete/', views.delete_prize_view, name='delete_prize'),
    path('account/', views.account_view, name='account'),
    path('security/', views.security_view, name='security'),
    path('logs/', views.logs_view, name='logs'),
    path('sessions/<int:session_id>/<str:action>/', views.admin_session_action, name='session_action'),
    path('settings/backup/', views.backup_database, name='backup_database'),
    path('issues/', views.issues_view, name='issues'),
    path('issues/<int:issue_id>/update/', views.update_issue_status, name='update_issue_status'),
    path('issues/<int:issue_id>/delete/', views.delete_issue, name='delete_issue'),
]

