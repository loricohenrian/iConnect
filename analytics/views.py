"""
Analytics Views — Predictive and Prescriptive Analytics
"""
from datetime import timedelta
from django.shortcuts import render
from django.utils import timezone
from django.db.models import Sum, Count, Avg
from django.db.models.functions import TruncDate

from sessions_app.models import Session, Plan
from dashboard.models import DailyRevenueSummary


def predictive(request):
    """Revenue forecasting page."""
    today = timezone.now().date()
    month_ago = today - timedelta(days=30)

    # Get daily revenue data for the past 30 days
    daily_data = Session.objects.filter(
        time_in__date__gte=month_ago,
        status__in=['active', 'expired']
    ).annotate(
        day=TruncDate('time_in')
    ).values('day').annotate(
        revenue=Sum('amount_paid'),
        sessions=Count('id')
    ).order_by('day')

    daily_list = list(daily_data)

    # Simple moving average forecast (7-day window)
    revenues = [d['revenue'] for d in daily_list]

    if len(revenues) >= 7:
        sma_7 = sum(revenues[-7:]) / 7
        forecast_week = round(sma_7 * 7)
        forecast_month = round(sma_7 * 30)
    else:
        avg = sum(revenues) / len(revenues) if revenues else 0
        sma_7 = avg
        forecast_week = round(avg * 7)
        forecast_month = round(avg * 30)

    # Trend direction
    if len(revenues) >= 14:
        first_half = sum(revenues[-14:-7]) / 7
        second_half = sum(revenues[-7:]) / 7
        trend = 'up' if second_half > first_half else 'down' if second_half < first_half else 'flat'
        trend_pct = ((second_half - first_half) / first_half * 100) if first_half > 0 else 0
    else:
        trend = 'flat'
        trend_pct = 0

    context = {
        'daily_data': daily_list,
        'daily_avg': round(sma_7, 0),
        'forecast_week': forecast_week,
        'forecast_month': forecast_month,
        'trend': trend,
        'trend_pct': round(abs(trend_pct), 1),
        'active_page': 'predictive',
    }
    return render(request, 'analytics/predictive.html', context)


def pricing(request):
    """Pricing recommendations page."""
    today = timezone.now().date()
    month_ago = today - timedelta(days=30)

    # Get plan statistics
    plan_stats = Session.objects.filter(
        time_in__date__gte=month_ago,
        status__in=['active', 'expired']
    ).values('plan__name', 'plan__price', 'plan__duration_minutes').annotate(
        usage_count=Count('id'),
        total_revenue=Sum('amount_paid'),
        avg_duration=Avg('duration_minutes_purchased')
    ).order_by('-usage_count')

    # Generate recommendations
    recommendations = []
    plan_list = list(plan_stats)

    if plan_list:
        most_popular = plan_list[0]
        least_popular = plan_list[-1] if len(plan_list) > 1 else None

        recommendations.append({
            'type': 'success',
            'icon': 'bi-star',
            'title': f'Most popular: {most_popular["plan__name"]}',
            'message': f'With {most_popular["usage_count"]} sessions, this plan generates the most revenue. Consider keeping this price point.',
        })

        if least_popular and least_popular['usage_count'] < most_popular['usage_count'] * 0.3:
            recommendations.append({
                'type': 'warning',
                'icon': 'bi-exclamation-triangle',
                'title': f'Low demand: {least_popular["plan__name"]}',
                'message': f'Only {least_popular["usage_count"]} sessions. Consider adjusting the price or duration to increase appeal.',
            })

        # Check if a mid-tier plan could help
        if len(plan_list) <= 2:
            recommendations.append({
                'type': 'info',
                'icon': 'bi-lightbulb',
                'title': 'Consider adding a mid-tier plan',
                'message': 'Having 3-4 plan options gives students more choices and can increase revenue.',
            })

    context = {
        'plan_stats': plan_list,
        'recommendations': recommendations,
        'active_page': 'pricing',
    }
    return render(request, 'analytics/pricing.html', context)
