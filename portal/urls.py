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

    # Captive portal connectivity check probe endpoints
    path('generate_204', views.captive_portal_probe, name='probe_generate_204'),
    path('gen_204', views.captive_portal_probe, name='probe_gen_204'),
    path('hotspot-detect.html', views.captive_portal_probe, name='probe_apple_hotspot'),
    path('connecttest.txt', views.captive_portal_probe, name='probe_msft_connecttest'),
    path('ncsi.txt', views.captive_portal_probe, name='probe_msft_ncsi'),
    path('success.txt', views.captive_portal_probe, name='probe_firefox_success'),
    path('canonical-check.txt', views.captive_portal_probe, name='probe_canonical'),
    path('check_network_status.txt', views.captive_portal_probe, name='probe_network_status'),
]

