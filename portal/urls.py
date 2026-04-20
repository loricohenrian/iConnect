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
]
