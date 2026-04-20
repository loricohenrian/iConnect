"""
Dashboard API Serializers
"""
from rest_framework import serializers
from .models import Announcement, RevenueGoal, ProjectCost, DailyRevenueSummary


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = ['id', 'message', 'is_active', 'created_at', 'updated_at']


class RevenueGoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = RevenueGoal
        fields = ['id', 'period', 'target_amount', 'created_at']


class ProjectCostSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectCost
        fields = ['id', 'description', 'amount', 'date_added']


class DailyRevenueSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyRevenueSummary
        fields = ['id', 'date', 'total_revenue', 'total_sessions', 'avg_session_minutes', 'peak_hour']
