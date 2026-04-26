"""
Dashboard API URLs
"""
from django.urls import path
from . import views

urlpatterns = [
    path('announcements/', views.announcements_api, name='announcements-api'),
    path('dashboard/stats/', views.dashboard_stats_api, name='dashboard-stats-api'),
    path('dashboard/system/', views.system_stats_api, name='system-stats-api'),
    path('dashboard/heatmap/', views.heatmap_data_api, name='heatmap-data-api'),
    path('dashboard/revenue/', views.revenue_data_api, name='revenue-data-api'),
]
