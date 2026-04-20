from django.contrib import admin
from .models import Announcement, RevenueGoal, ProjectCost, DailyRevenueSummary


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('message', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)


@admin.register(RevenueGoal)
class RevenueGoalAdmin(admin.ModelAdmin):
    list_display = ('period', 'target_amount', 'created_at')
    list_filter = ('period',)


@admin.register(ProjectCost)
class ProjectCostAdmin(admin.ModelAdmin):
    list_display = ('description', 'amount', 'date_added')


@admin.register(DailyRevenueSummary)
class DailyRevenueSummaryAdmin(admin.ModelAdmin):
    list_display = ('date', 'total_revenue', 'total_sessions', 'avg_session_minutes', 'peak_hour')
    list_filter = ('date',)
