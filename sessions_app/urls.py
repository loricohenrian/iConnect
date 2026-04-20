"""
Session Management API URLs
"""
from django.urls import path
from . import views

app_name = 'sessions_app'

urlpatterns = [
    # Coin detection
    path('coin-inserted/', views.coin_inserted, name='coin-inserted'),

    # Session management
    path('session/start/request/', views.session_start_request, name='session-start-request'),
    path('session/start/request-status/', views.session_start_request_status, name='session-start-request-status'),
    path('session/start/', views.session_start, name='session-start'),
    path('session/extend/', views.session_extend, name='session-extend'),
    path('session/end/', views.session_end, name='session-end'),
    path('session/status/', views.session_status, name='session-status'),

    # Device management
    path('connected-users/', views.connected_users, name='connected-users'),
    path('bandwidth/', views.bandwidth_usage, name='bandwidth'),
    path('whitelist/', views.whitelist_device, name='whitelist'),
    path('signal-strength/', views.signal_strength, name='signal-strength'),
    path('speed-test/', views.speed_test, name='speed-test'),

    # Plans
    path('plans/', views.plans_list, name='plans-list'),
]
