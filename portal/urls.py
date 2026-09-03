"""
Captive Portal URLs
"""
from django.urls import path
from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.index, name='index'),
    path('session/', views.session_page, name='session'),
    path('history/', views.history, name='history'),
    path('manual/', views.manual, name='manual'),
    path('spin/', views.spin_wheel_view, name='spin_wheel'),
    path('api/execute_spin/', views.api_execute_spin, name='api_execute_spin'),
    path('api/spin-data/', views.api_spin_data, name='api_spin_data'),
    path('api/report-issue/', views.api_report_issue, name='api_report_issue'),
    path('sw.js', views.service_worker, name='service_worker'),
]

