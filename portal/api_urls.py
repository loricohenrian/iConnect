"""
Captive Portal API URLs
"""
from django.urls import path
from . import views

urlpatterns = [
    path('live-data/', views.live_data, name='portal-live-data'),
]
