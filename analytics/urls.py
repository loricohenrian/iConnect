"""
Analytics URLs
"""
from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('predictive/', views.predictive, name='predictive'),
    path('pricing/', views.pricing, name='pricing'),
]
