"""
Dashboard Template View URLs
"""
from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('login/', views.dashboard_login, name='login'),
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
    path('account/', views.account_view, name='account'),
    path('sessions/<int:session_id>/<str:action>/', views.admin_session_action, name='session_action'),
]
