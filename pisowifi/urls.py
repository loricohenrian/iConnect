"""
iConnect URL Configuration
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django Admin
    path('admin/', admin.site.urls),

    # API Endpoints
    path('api/', include('sessions_app.urls')),
    path('api/', include('dashboard.urls')),
    path('api/portal/', include('portal.api_urls')),
    path('api/analytics/', include('analytics.urls')),

    # User-facing Captive Portal
    path('', include('portal.urls')),

    # Admin Dashboard
    path('dashboard/', include('dashboard.urls_dashboard')),

    # Reports
    path('reports/', include('reports.urls')),
]

# Serve media/static in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

