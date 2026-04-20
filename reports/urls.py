"""
Reports URLs
"""
from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('generate/', views.generate_report_view, name='generate'),
]
