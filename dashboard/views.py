"""
Dashboard Views — API endpoints and template views for admin dashboard
"""
import csv
from datetime import timedelta, date
from decimal import Decimal, InvalidOperation
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.db.models import Sum, Count, Avg, F, Q
from django.db.models.deletion import ProtectedError
from django.db.models.functions import TruncDate, TruncHour, ExtractHour, ExtractWeekDay
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils.dateparse import parse_date
from django.http import HttpResponse, JsonResponse
from django.core.cache import cache
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Announcement, RevenueGoal, ProjectCost, DailyRevenueSummary, SystemSettings, IssueReport
from .serializers import AnnouncementSerializer, RevenueGoalSerializer, ProjectCostSerializer
from sessions_app import iptables
from sessions_app.models import Session, CoinEvent, Plan, WhitelistedDevice, SuspiciousDevice, PurchaseTransaction
from django.conf import settings


logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('audit')


def _is_dashboard_admin(user):
    return user.is_authenticated and user.is_staff


def _client_ip(request):
    real_ip = request.META.get("HTTP_X_REAL_IP", "")
    if real_ip:
        return real_ip.strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _check_rate_limit(key, max_attempts, window_seconds):
    try:
        current = cache.get(key, 0)
        if current >= max_attempts:
            return True

        cache.add(key, 0, timeout=window_seconds)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window_seconds)
        return False
    except Exception as exc:
        # Fail open when cache backend is unavailable to avoid auth endpoint 500s.
        logger.warning('dashboard_login_rate_limit_cache_unavailable key=%s error=%s', key, exc)
        return False


def dashboard_login(request):
    """Dashboard login page."""
    if _is_dashboard_admin(request.user):
        return redirect('dashboard:overview')

    error_message = ''
    requested_next = request.GET.get('next') or request.POST.get('next') or '/dashboard/'
    if url_has_allowed_host_and_scheme(
        requested_next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = requested_next
    else:
        next_url = '/dashboard/'

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        ip = _client_ip(request)
        lock_key = f'dashboard-login:{ip}:{username.lower()}'
        max_attempts = getattr(settings, 'PISONET_LOGIN_MAX_ATTEMPTS', 5)
        window_seconds = getattr(settings, 'PISONET_LOGIN_WINDOW_SECONDS', 300)

        if _check_rate_limit(lock_key, max_attempts, window_seconds):
            audit_logger.warning('event=dashboard_login_rate_limited ip=%s username=%s', ip, username)
            error_message = 'Too many login attempts. Please try again later.'
            return render(request, 'dashboard/login.html', {
                'error_message': error_message,
                'next': next_url,
            })

        user = authenticate(request, username=username, password=password)
        if user and user.is_staff:
            try:
                cache.delete(lock_key)
            except Exception as exc:
                logger.warning('dashboard_login_lock_clear_cache_unavailable key=%s error=%s', lock_key, exc)
            login(request, user)
            audit_logger.info('event=dashboard_login_success user=%s ip=%s', user.username, ip)
            return redirect(next_url)
        audit_logger.warning('event=dashboard_login_failed username=%s ip=%s', username, ip)
        error_message = 'Invalid username or password.'

    return render(request, 'dashboard/login.html', {
        'error_message': error_message,
        'next': next_url,
    })


@require_POST
def dashboard_logout(request):
    if request.user.is_authenticated:
        audit_logger.info('event=dashboard_logout user=%s ip=%s', request.user.username, _client_ip(request))
    logout(request)
    return redirect('dashboard:login')


# ============================================
# API ENDPOINTS
# ============================================

@api_view(['GET', 'POST'])
def announcements_api(request):
    """
    GET /api/announcements/ — Returns active announcements
    POST /api/announcements/ — Creates new announcement
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    if request.method == 'GET':
        announcements = Announcement.objects.filter(is_active=True)
        return Response({
            'announcements': AnnouncementSerializer(announcements, many=True).data
        })

    elif request.method == 'POST':
        serializer = AnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                'status': 'success',
                'announcement': serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def dashboard_stats_api(request):
    """
    GET /api/dashboard/stats/ — Returns dashboard summary stats
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    today = timezone.localdate()
    week_ago = today - timedelta(days=7)

    # Revenue today (all physical coins inserted today)
    revenue_today = CoinEvent.objects.filter(
        timestamp__date=today
    ).aggregate(total=Sum('amount'))['total'] or 0

    # Connected users
    connected_count = Session.objects.filter(status='active').count()
    whitelisted_count = WhitelistedDevice.objects.count()

    # Total bandwidth today
    bandwidth_today = Session.objects.filter(
        time_in__date=today
    ).aggregate(total=Sum('bandwidth_used_mb'))['total'] or 0

    # ROI
    total_cost = ProjectCost.total_cost()
    total_revenue = CoinEvent.objects.aggregate(total=Sum('amount'))['total'] or 0

    first_session = Session.objects.order_by('time_in').first()
    days_operating = max((timezone.now() - first_session.time_in).days, 1) if first_session else 0
    
    from dashboard.models import OperatingExpense
    total_expenses = OperatingExpense.calculate_total_expenses(days_operating)
    
    net_profit = total_revenue - total_expenses
    roi_percentage = (net_profit / total_cost * 100) if total_cost > 0 else 0

    # Revenue last 7 days
    daily_revenue = Session.objects.filter(
        time_in__date__gte=week_ago,
        status__in=['active', 'expired', 'paused']
    ).annotate(
        day=TruncDate('time_in')
    ).values('day').annotate(
        revenue=Sum('amount_paid'),
        sessions=Count('id')
    ).order_by('day')

    # Sessions today
    sessions_today = Session.objects.filter(time_in__date=today).count()

    # Recent sessions (latest 10)
    recent_qs = Session.objects.select_related('plan').order_by('-time_in')[:10]
    recent_sessions_data = []
    for s in recent_qs:
        recent_sessions_data.append({
            'id': s.id,
            'device_name': s.device_name or 'Unknown',
            'mac_address': s.mac_address,
            'plan_name': s.plan.name if s.plan else 'Custom',
            'amount_paid': str(s.amount_paid),
            'time_in': s.time_in.strftime('%b %d, %I:%M %p') if s.time_in else '-',
            'time_remaining_seconds': max(0, int(s.time_remaining_seconds)),
            'time_remaining_display': s.time_remaining_display,
            'status': s.status,
            'status_display': s.get_status_display(),
        })

    # Solar savings
    system_watts = getattr(settings, 'PISONET_SYSTEM_WATTAGE', 18)
    elec_rate = getattr(settings, 'PISONET_ELECTRICITY_RATE', 11.0)
    hours_today = timezone.now().hour
    daily_savings = (system_watts / 1000) * hours_today * elec_rate

    return Response({
        'revenue_today': revenue_today,
        'connected_users': connected_count,
        'whitelisted_devices': whitelisted_count,
        'total_connected': connected_count + whitelisted_count,
        'bandwidth_today_mb': round(bandwidth_today, 1),
        'roi_percentage': round(roi_percentage, 1),
        'total_cost': total_cost,
        'total_revenue': total_revenue,
        'sessions_today': sessions_today,
        'daily_revenue': list(daily_revenue),
        'solar_savings_today': round(daily_savings, 2),
        'recent_sessions': recent_sessions_data,
    })


@api_view(['GET'])
def system_stats_api(request):
    """System hardware stats (CPU temp, load, RAM, disk)."""
    import shutil
    import os
    import subprocess

    stats = {
        'cpu_temp': 'N/A',
        'cpu_load': 'N/A',
        'cpu_load_raw': 0,
        'cpu_count': 1,
        'ram_used': 'N/A',
        'ram_total': 'N/A',
        'ram_remaining': 'N/A',
        'ram_percent': 0,
        'disk_used': 'N/A',
        'disk_total': 'N/A',
        'disk_remaining': 'N/A',
        'disk_percent': 0,
    }

    # CPU Temperature
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_raw = int(f.read().strip())
            stats['cpu_temp'] = f"{temp_raw / 1000:.1f}°C"
    except Exception:
        pass

    # CPU Count
    try:
        stats['cpu_count'] = os.cpu_count() or 1
    except Exception:
        pass

    # CPU Load (1-min average)
    try:
        with open('/proc/loadavg', 'r') as f:
            load = float(f.read().split()[0])
            stats['cpu_load'] = f"{load:.2f}"
            stats['cpu_load_raw'] = round(min((load / stats['cpu_count']) * 100, 100), 1)
    except Exception:
        try:
            load = os.getloadavg()[0]
            stats['cpu_load'] = f"{load:.2f}"
            stats['cpu_load_raw'] = round(min((load / stats['cpu_count']) * 100, 100), 1)
        except Exception:
            pass

    # RAM
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].strip().split()[0])  # kB
                    meminfo[key] = val
            total_mb = meminfo.get('MemTotal', 0) / 1024
            available_mb = meminfo.get('MemAvailable', 0) / 1024
            used_mb = total_mb - available_mb
            remaining_mb = available_mb
            stats['ram_total'] = f"{total_mb:.0f}Mb"
            stats['ram_used'] = f"{used_mb:.0f}Mb"
            stats['ram_remaining'] = f"{remaining_mb:.0f}Mb"
            stats['ram_percent'] = round((used_mb / total_mb) * 100) if total_mb > 0 else 0
    except Exception:
        pass

    # Disk
    try:
        usage = shutil.disk_usage('/')
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        stats['disk_total'] = f"{total_gb:.2f}GB"
        stats['disk_used'] = f"{used_gb:.2f}GB"
        stats['disk_remaining'] = f"{free_gb:.2f}GB"
        stats['disk_percent'] = round((usage.used / usage.total) * 100) if usage.total > 0 else 0
    except Exception:
        pass

    # Internet Status Check
    try:
        subprocess.run(
            ['ping', '-c', '1', '-W', '1', '8.8.8.8'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        stats['internet_online'] = True
    except Exception:
        stats['internet_online'] = False

    return Response(stats)


@api_view(['GET'])
def heatmap_data_api(request):
    """
    GET /api/dashboard/heatmap/ — Returns peak hours heatmap data
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    week_ago = timezone.localdate() - timedelta(days=7)

    sessions = Session.objects.filter(
        time_in__date__gte=week_ago
    ).annotate(
        weekday=ExtractWeekDay('time_in'),
        hour=ExtractHour('time_in')
    ).values('weekday', 'hour').annotate(
        count=Count('id')
    ).order_by('weekday', 'hour')

    return Response({'heatmap': list(sessions)})


@api_view(['GET'])
def revenue_data_api(request):
    """
    GET /api/dashboard/revenue/ — Returns detailed revenue data
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    period = request.query_params.get('period', 'weekly')
    today = timezone.localdate()

    if period == 'daily':
        start_date = today
    elif period == 'weekly':
        start_date = today - timedelta(days=7)
    elif period == 'monthly':
        start_date = today - timedelta(days=30)
    else:
        start_date = today - timedelta(days=365)

    revenue_data = Session.objects.filter(
        time_in__date__gte=start_date,
        status__in=['active', 'expired']
    ).annotate(
        day=TruncDate('time_in')
    ).values('day').annotate(
        revenue=Sum('amount_paid'),
        sessions=Count('id'),
        avg_minutes=Avg('duration_minutes_purchased')
    ).order_by('day')

    # Revenue goal
    goal = RevenueGoal.objects.filter(
        period='daily' if period == 'daily' else 'weekly'
    ).first()
    goal_amount = goal.target_amount if goal else 0

    period_revenue_total = Session.objects.filter(
        time_in__date__gte=start_date,
        status__in=['active', 'expired']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0

    threshold_pct = float(getattr(settings, 'PISONET_LOW_REVENUE_ALERT_THRESHOLD_PCT', 70))
    threshold_amount = round(goal_amount * (threshold_pct / 100), 2) if goal_amount else 0
    low_revenue_triggered = goal_amount > 0 and period_revenue_total < threshold_amount

    # Plan breakdown
    plan_stats = Session.objects.filter(
        time_in__date__gte=start_date,
        status__in=['active', 'expired']
    ).values('plan__name', 'plan__price').annotate(
        count=Count('id'),
        total=Sum('amount_paid')
    ).order_by('-total')

    return Response({
        'revenue_data': list(revenue_data),
        'goal_amount': goal_amount,
        'period_revenue_total': period_revenue_total,
        'low_revenue_alert': {
            'enabled': goal_amount > 0,
            'triggered': low_revenue_triggered,
            'threshold_pct': threshold_pct,
            'threshold_amount': threshold_amount,
            'message': (
                f'Revenue is below {threshold_pct:.0f}% threshold '
                f'(₱{int(period_revenue_total):,} vs ₱{int(threshold_amount):,}).'
                if low_revenue_triggered
                else ''
            ),
        },
        'plan_breakdown': list(plan_stats),
        'period': period,
    })


@api_view(['GET'])
def revenue_live_api(request):
    """
    GET /api/dashboard/revenue/live/ — Real-time revenue statistics, chart data, and filtered sessions
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    period = request.GET.get('period', 'today')
    custom_start = request.GET.get('start_date')
    custom_end = request.GET.get('end_date')

    today = timezone.localdate()
    start_date = None
    end_date = None

    if period == 'custom':
        if custom_start:
            start_date = parse_date(custom_start)
        if custom_end:
            end_date = parse_date(custom_end)
    elif period == 'today':
        start_date = today
        end_date = today
    elif period == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif period == 'month':
        start_date = today.replace(day=1)
        end_date = today
    elif period == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif period == 'all':
        pass
    else:
        period = 'today'
        start_date = today
        end_date = today

    sessions_qs = Session.objects.select_related('plan').all().order_by('-time_in')
    coins_qs = CoinEvent.objects.all()
    purchases_qs = PurchaseTransaction.objects.all()

    if start_date:
        sessions_qs = sessions_qs.filter(time_in__date__gte=start_date)
        coins_qs = coins_qs.filter(timestamp__date__gte=start_date)
        purchases_qs = purchases_qs.filter(timestamp__date__gte=start_date)
    if end_date:
        sessions_qs = sessions_qs.filter(time_in__date__lte=end_date)
        coins_qs = coins_qs.filter(timestamp__date__lte=end_date)
        purchases_qs = purchases_qs.filter(timestamp__date__lte=end_date)

    total_sales = coins_qs.aggregate(total=Sum('amount'))['total'] or 0
    total_sessions = sessions_qs.count()
    avg_transaction = round(total_sales / total_sessions, 2) if total_sessions > 0 else 0

    plan_stats = purchases_qs.values('plan__name').annotate(
        revenue=Sum('amount')
    ).order_by('-revenue')

    plan_labels = [p['plan__name'] for p in plan_stats]
    plan_data = [float(p['revenue'] or 0) for p in plan_stats]

    status_filter = request.GET.get('status', '')
    if status_filter:
        sessions_qs = sessions_qs.filter(status=status_filter)

    page = request.GET.get('page', 1)
    paginator = Paginator(sessions_qs, 20)
    try:
        sessions_page = paginator.page(page)
    except PageNotAnInteger:
        sessions_page = paginator.page(1)
    except EmptyPage:
        sessions_page = paginator.page(paginator.num_pages)

    sessions_data = []
    for s in sessions_page:
        sessions_data.append({
            'id': s.id,
            'device_name': s.device_name or 'Unknown',
            'mac_address': s.mac_address,
            'plan_name': s.plan.name if s.plan else 'Custom',
            'time_in_date': s.time_in.strftime('%b %d, %Y') if s.time_in else '-',
            'time_in_time': s.time_in.strftime('%I:%M %p') if s.time_in else '-',
            'ip_address': s.ip_address or '-',
            'duration_minutes_purchased': s.duration_minutes_purchased,
            'amount_paid': str(s.amount_paid),
            'status': s.status,
            'status_display': s.get_status_display(),
        })

    return Response({
        'total_sales': float(total_sales),
        'total_sessions': total_sessions,
        'avg_transaction': float(avg_transaction),
        'plan_labels': plan_labels,
        'plan_data': plan_data,
        'sessions': sessions_data,
        'start_index': sessions_page.start_index() if total_sessions > 0 else 0,
        'end_index': sessions_page.end_index() if total_sessions > 0 else 0,
        'total_count': paginator.count,
        'page': sessions_page.number,
        'num_pages': paginator.num_pages,
    })


@api_view(['GET'])
def sessions_live_api(request):
    """
    GET /api/dashboard/sessions/live/ — Real-time active, paused, and connected session list & stats
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')
    period = request.GET.get('period', 'today')

    sessions = Session.objects.select_related('plan').all()

    now = timezone.now()
    today = timezone.localdate()
    if period == 'today' or not period:
        sessions = sessions.filter(
            Q(time_in__date=today) | Q(status__in=['active', 'paused'])
        )
    elif period == 'week':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=7))
    elif period == 'month':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=30))
    elif period == 'year':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=365))

    total_users = sessions.count()
    connected_users = sessions.filter(status='active').count()
    paused_users = sessions.filter(status='paused').count()
    disconnected_users = sessions.filter(status='expired').count()

    if status_filter:
        sessions = sessions.filter(status=status_filter)
    if search:
        sessions = sessions.filter(
            Q(mac_address__icontains=search) |
            Q(device_name__icontains=search) |
            Q(ip_address__icontains=search)
        )

    suspicious_macs = set(SuspiciousDevice.objects.filter(status='new').values_list('mac_address', flat=True))

    session_list = []
    for s in sessions[:100]:
        session_list.append({
            'id': s.id,
            'device_name': s.device_name or 'Unknown',
            'mac_address': s.mac_address,
            'ip_address': s.ip_address or '—',
            'plan_name': s.plan.name if s.plan else 'Custom',
            'amount_paid': str(s.amount_paid),
            'time_in': s.time_in.strftime('%b %d, %I:%M %p') if s.time_in else '—',
            'time_out': s.time_out.strftime('%b %d, %I:%M %p') if s.time_out else '—',
            'duration_minutes_purchased': s.duration_minutes_purchased,
            'group_code': s.session_group.group_code if s.session_group else None,
            'time_remaining_seconds': max(0, int(s.time_remaining_seconds)),
            'time_remaining_display': s.time_remaining_display,
            'bandwidth_used_mb': round(s.bandwidth_used_mb, 1),
            'status': s.status,
            'status_display': s.get_status_display(),
            'is_suspicious': s.mac_address in suspicious_macs,
        })

    return Response({
        'total_users': total_users,
        'connected_users': connected_users,
        'paused_users': paused_users,
        'disconnected_users': disconnected_users,
        'sessions': session_list,
    })


# ============================================
# TEMPLATE VIEWS (Admin Dashboard Pages)
# ============================================

@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def overview(request):
    """Admin dashboard overview page."""
    today = timezone.localdate()

    # Revenue = all coins inserted (coins are physically in the box)
    revenue_today = CoinEvent.objects.filter(
        timestamp__date=today
    ).aggregate(total=Sum('amount'))['total'] or 0

    start_of_week = today - timedelta(days=today.weekday())
    revenue_this_week = CoinEvent.objects.filter(
        timestamp__date__gte=start_of_week
    ).aggregate(total=Sum('amount'))['total'] or 0

    start_of_month = today.replace(day=1)
    revenue_this_month = CoinEvent.objects.filter(
        timestamp__date__gte=start_of_month
    ).aggregate(total=Sum('amount'))['total'] or 0

    connected = Session.objects.filter(status='active').count()
    whitelisted = WhitelistedDevice.objects.count()
    sessions_today = Session.objects.filter(time_in__date=today).count()

    total_cost = ProjectCost.total_cost()
    total_revenue = CoinEvent.objects.aggregate(total=Sum('amount'))['total'] or 0
    
    first_session = Session.objects.order_by('time_in').first()
    days_operating = max((timezone.now() - first_session.time_in).days, 1) if first_session else 0
    
    from dashboard.models import OperatingExpense
    total_expenses = OperatingExpense.calculate_total_expenses(days_operating)
    
    net_profit = total_revenue - total_expenses
    roi_pct = (net_profit / total_cost * 100) if total_cost > 0 else 0

    recent_sessions = Session.objects.select_related('plan').all()[:5]
    announcements = Announcement.objects.filter(is_active=True)

    # Solar savings
    system_watts = getattr(settings, 'PISONET_SYSTEM_WATTAGE', 18)
    elec_rate = getattr(settings, 'PISONET_ELECTRICITY_RATE', 11.0)
    monthly_savings = (system_watts / 1000) * 24 * 30 * elec_rate

    context = {
        'revenue_today': revenue_today,
        'revenue_this_week': revenue_this_week,
        'revenue_this_month': revenue_this_month,
        'start_of_week_date': start_of_week.strftime('%b %d'),
        'current_month_name': today.strftime('%B %Y'),
        'connected_users': connected,
        'whitelisted_count': whitelisted,
        'total_connected': connected + whitelisted,
        'sessions_today': sessions_today,
        'roi_percentage': round(roi_pct, 1),
        'total_cost': total_cost,
        'total_revenue': total_revenue,
        'recent_sessions': recent_sessions,
        'announcements': announcements,
        'monthly_solar_savings': round(monthly_savings, 2),
        'active_page': 'overview',
    }
    return render(request, 'dashboard/overview.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def revenue(request):
    """Revenue monitoring page."""
    import json
    
    # Handle POST for resetting sales
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reset_sales':
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            
            # If dates provided, filter. Else, all time.
            sess_qs = Session.objects.all()
            coin_qs = CoinEvent.objects.all()
            purch_qs = PurchaseTransaction.objects.all()
            
            if start_date_str:
                s_date = parse_date(start_date_str)
                if s_date:
                    sess_qs = sess_qs.filter(time_in__date__gte=s_date)
                    coin_qs = coin_qs.filter(timestamp__date__gte=s_date)
                    purch_qs = purch_qs.filter(timestamp__date__gte=s_date)
            if end_date_str:
                e_date = parse_date(end_date_str)
                if e_date:
                    sess_qs = sess_qs.filter(time_in__date__lte=e_date)
                    coin_qs = coin_qs.filter(timestamp__date__lte=e_date)
                    purch_qs = purch_qs.filter(timestamp__date__lte=e_date)
                    
            deleted_sessions = sess_qs.count()
            sess_qs.delete()
            coin_qs.delete()
            purch_qs.delete()
            # Redirect to avoid form resubmission
            return redirect(f"{request.path}?reset=success&deleted={deleted_sessions}")

    # Process GET parameters for date filtering
    period = request.GET.get('period', 'today')
    custom_start = request.GET.get('start_date')
    custom_end = request.GET.get('end_date')
    
    today = timezone.localdate()
    now = timezone.now()
    
    start_date = None
    end_date = None
    
    if period == 'custom':
        if custom_start:
            start_date = parse_date(custom_start)
        if custom_end:
            end_date = parse_date(custom_end)
    elif period == 'today':
        start_date = today
        end_date = today
    elif period == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif period == 'month':
        start_date = today.replace(day=1)
        end_date = today
    elif period == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif period == 'all':
        pass
    else:
        # fallback to today
        period = 'today'
        start_date = today
        end_date = today

    # Base querysets
    sessions_qs = Session.objects.select_related('plan').all().order_by('-time_in')
    coins_qs = CoinEvent.objects.all()
    purchases_qs = PurchaseTransaction.objects.all()

    # Apply date filters
    if start_date:
        sessions_qs = sessions_qs.filter(time_in__date__gte=start_date)
        coins_qs = coins_qs.filter(timestamp__date__gte=start_date)
        purchases_qs = purchases_qs.filter(timestamp__date__gte=start_date)
    if end_date:
        sessions_qs = sessions_qs.filter(time_in__date__lte=end_date)
        coins_qs = coins_qs.filter(timestamp__date__lte=end_date)
        purchases_qs = purchases_qs.filter(timestamp__date__lte=end_date)

    # 1. Top Row Metrics
    total_sales = coins_qs.aggregate(total=Sum('amount'))['total'] or 0
    total_sessions = sessions_qs.count()
    avg_transaction = round(total_sales / total_sessions, 2) if total_sessions > 0 else 0

    # 2. Plan Breakdown (Bar Chart Data)
    plan_stats = purchases_qs.values('plan__name').annotate(
        revenue=Sum('amount')
    ).order_by('-revenue')
    
    plan_labels = [p['plan__name'] for p in plan_stats]
    plan_data = [p['revenue'] for p in plan_stats]

    # 3. Sessions List & Pagination
    # Support status filtering
    status_filter = request.GET.get('status', '')
    if status_filter:
        sessions_qs = sessions_qs.filter(status=status_filter)
        
    page = request.GET.get('page', 1)
    paginator = Paginator(sessions_qs, 20) # 20 items per page
    try:
        sessions_page = paginator.page(page)
    except PageNotAnInteger:
        sessions_page = paginator.page(1)
    except EmptyPage:
        sessions_page = paginator.page(paginator.num_pages)

    context = {
        'active_page': 'revenue',
        'period': period,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
        'total_sales': total_sales,
        'total_sessions': total_sessions,
        'avg_transaction': avg_transaction,
        'plan_labels': json.dumps(plan_labels),
        'plan_data': json.dumps(plan_data),
        'sessions': sessions_page,
        'status_filter': status_filter,
    }
    return render(request, 'dashboard/revenue.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def sessions_view(request):
    """Session logs page."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')
    period = request.GET.get('period', 'today')

    sessions = Session.objects.select_related('plan').all()

    # Time period filter (default: today)
    now = timezone.now()
    today = timezone.localdate()
    if period == 'today' or not period:
        # Include sessions started today OR any session that is currently active/paused
        sessions = sessions.filter(
            Q(time_in__date=today) | Q(status__in=['active', 'paused'])
        )
    elif period == 'week':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=7))
    elif period == 'month':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=30))
    elif period == 'year':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=365))
    # 'all' = no filter

    # Calculate metrics before applying status and search filters
    total_users = sessions.count()
    connected_users = sessions.filter(status='active').count()
    paused_users = sessions.filter(status='paused').count()
    disconnected_users = sessions.filter(status='expired').count()

    if status_filter:
        sessions = sessions.filter(status=status_filter)
    if search:
        sessions = sessions.filter(
            Q(mac_address__icontains=search) |
            Q(device_name__icontains=search) |
            Q(ip_address__icontains=search)
        )

    suspicious_macs = set(SuspiciousDevice.objects.filter(status='new').values_list('mac_address', flat=True))

    context = {
        'sessions': sessions[:100],
        'status_filter': status_filter,
        'search': search,
        'period': period,
        'active_page': 'sessions',
        'total_users': total_users,
        'connected_users': connected_users,
        'paused_users': paused_users,
        'disconnected_users': disconnected_users,
        'suspicious_macs': suspicious_macs,
    }
    return render(request, 'dashboard/sessions.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def export_sessions_csv(request):
    """Export session logs as CSV file."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')
    period = request.GET.get('period', 'today')
    custom_start = request.GET.get('start_date')
    custom_end = request.GET.get('end_date')

    sessions = Session.objects.select_related('plan').all()

    now = timezone.now()
    today = timezone.localdate()
    
    if period == 'custom':
        if custom_start:
            s_date = parse_date(custom_start)
            if s_date:
                sessions = sessions.filter(time_in__date__gte=s_date)
        if custom_end:
            e_date = parse_date(custom_end)
            if e_date:
                sessions = sessions.filter(time_in__date__lte=e_date)
    elif period == 'today' or not period:
        sessions = sessions.filter(time_in__date=today)
    elif period == 'week':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=7))
    elif period == 'month':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=30))
    elif period == 'year':
        sessions = sessions.filter(time_in__gte=now - timedelta(days=365))

    if status_filter:
        sessions = sessions.filter(status=status_filter)
    if search:
        sessions = sessions.filter(
            Q(mac_address__icontains=search) |
            Q(device_name__icontains=search) |
            Q(ip_address__icontains=search)
        )

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"iconnect_sessions_{today.strftime('%Y%m%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff') # UTF-8 BOM for Microsoft Excel

    writer = csv.writer(response)
    writer.writerow([
        'Session ID', 'MAC Address', 'IP Address', 'Device Name',
        'Plan Name', 'Amount Paid (PHP)', 'Duration (Mins)',
        'Status', 'Time In', 'Time Out', 'Bandwidth Used (MB)'
    ])

    for session in sessions:
        time_in_str = timezone.localtime(session.time_in).strftime('%Y-%m-%d %H:%M:%S') if session.time_in else ''
        time_out_str = timezone.localtime(session.time_out).strftime('%Y-%m-%d %H:%M:%S') if session.time_out else ''
        writer.writerow([
            session.id,
            session.mac_address,
            session.ip_address or 'N/A',
            session.device_name or 'Unknown',
            session.plan.name if session.plan else 'Custom',
            session.amount_paid,
            session.duration_minutes_purchased,
            session.get_status_display(),
            time_in_str,
            time_out_str,
            round(session.bandwidth_used_mb, 2),
        ])

    audit_logger.info(
        "event=export_sessions_csv user=%s count=%d period=%s status=%s ip=%s",
        request.user.username, sessions.count(), period, status_filter, _client_ip(request)
    )

    return response


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def export_revenue_csv(request):
    """Export daily revenue summary logs as CSV file."""
    summaries = DailyRevenueSummary.objects.all().order_by('-date')
    today = timezone.localdate()

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"iconnect_revenue_summary_{today.strftime('%Y%m%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff') # UTF-8 BOM for Microsoft Excel

    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Total Revenue (PHP)', 'Total Sessions',
        'Avg Session Duration (Mins)', 'Peak Hour'
    ])

    for summary in summaries:
        peak_str = f"{summary.peak_hour:02d}:00" if summary.peak_hour is not None else 'N/A'
        writer.writerow([
            summary.date.strftime('%Y-%m-%d'),
            summary.total_revenue,
            summary.total_sessions,
            summary.avg_session_minutes,
            peak_str,
        ])

    audit_logger.info(
        "event=export_revenue_csv user=%s count=%d ip=%s",
        request.user.username, summaries.count(), _client_ip(request)
    )

    return response


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def reports(request):
    """Reports page with financial summary and analytics."""
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    # === Period Revenue (CoinEvent-based) ===
    revenue_today = CoinEvent.objects.filter(
        timestamp__date=today
    ).aggregate(total=Sum('amount'))['total'] or 0

    revenue_week = CoinEvent.objects.filter(
        timestamp__date__gte=week_ago
    ).aggregate(total=Sum('amount'))['total'] or 0

    revenue_month = CoinEvent.objects.filter(
        timestamp__date__gte=month_ago
    ).aggregate(total=Sum('amount'))['total'] or 0

    revenue_all_time = CoinEvent.objects.aggregate(
        total=Sum('amount')
    )['total'] or 0

    # === Sessions counts ===
    sessions_today = Session.objects.filter(time_in__date=today).count()
    sessions_week = Session.objects.filter(time_in__date__gte=week_ago).count()
    sessions_month = Session.objects.filter(time_in__date__gte=month_ago).count()
    sessions_total = Session.objects.count()

    # === Operating Expenses ===
    first_session = Session.objects.order_by('time_in').first()
    days_operating = max((timezone.now() - first_session.time_in).days, 1) if first_session else 0

    from dashboard.models import OperatingExpense
    total_expenses = OperatingExpense.calculate_total_expenses(days_operating)
    
    # We still need to pass operating_expenses to the template
    operating_expenses = OperatingExpense.objects.all()

    # === Financial Summary ===
    from dashboard.models import ProjectCost
    total_investment = ProjectCost.total_cost()
    net_profit = round(revenue_all_time - total_expenses, 2)
    roi_pct = round((net_profit / total_investment * 100), 1) if total_investment > 0 else 0

    # === Top Plans (Excluding ₱0 prizes) ===
    top_plans = Session.objects.filter(
        time_in__date__gte=month_ago,
        status__in=['active', 'expired', 'paused'],
        amount_paid__gt=0,
        plan__isnull=False
    ).exclude(plan__name__startswith="Prize:").values('plan__name', 'plan__price').annotate(
        count=Count('id'),
        total=Sum('amount_paid'),
    ).order_by('-count')[:5]

    # === Recent Sessions ===
    recent_sessions = Session.objects.select_related('plan').order_by('-time_in')[:10]

    # === Daily revenue for last 7 days (for chart) ===
    daily_revenue = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_rev = CoinEvent.objects.filter(
            timestamp__date=day
        ).aggregate(total=Sum('amount'))['total'] or 0
        daily_revenue.append({
            'date': day.strftime('%b %d'),
            'revenue': day_rev,
        })

    context = {
        # Period stats
        'revenue_today': revenue_today,
        'revenue_week': revenue_week,
        'revenue_month': revenue_month,
        'revenue_all_time': revenue_all_time,
        'sessions_today': sessions_today,
        'sessions_week': sessions_week,
        'sessions_month': sessions_month,
        'sessions_total': sessions_total,
        # Financial
        'total_expenses': total_expenses,
        'operating_expenses': operating_expenses,
        'net_profit': net_profit,
        'total_investment': total_investment,
        'roi_percentage': roi_pct,
        'days_operating': days_operating,
        # Data
        'top_plans': top_plans,
        'recent_sessions': recent_sessions,
        'daily_revenue': daily_revenue,
        'active_page': 'reports',
    }
    return render(request, 'dashboard/reports.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def heatmap(request):
    """Peak hours heatmap page."""
    context = {
        'active_page': 'heatmap',
    }
    return render(request, 'dashboard/heatmap.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def analytics_view(request):
    """User behavior analytics page with diagnostic & prescriptive insights."""
    period = request.GET.get('period', 'month')
    custom_start = request.GET.get('start_date')
    custom_end = request.GET.get('end_date')

    today = timezone.localdate()
    now = timezone.now()

    start_date = None
    end_date = None

    if period == 'custom':
        if custom_start:
            start_date = parse_date(custom_start)
        if custom_end:
            end_date = parse_date(custom_end)
    elif period == 'today':
        start_date = today
        end_date = today
    elif period == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif period == 'month':
        start_date = today - timedelta(days=30)
        end_date = today
    elif period == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif period == 'all':
        pass
    else:
        period = 'month'
        start_date = today - timedelta(days=30)
        end_date = today

    # Base sessions queryset
    sessions_qs = Session.objects.filter(status__in=['active', 'expired', 'paused'])
    if start_date:
        sessions_qs = sessions_qs.filter(time_in__date__gte=start_date)
    if end_date:
        sessions_qs = sessions_qs.filter(time_in__date__lte=end_date)

    # 1. Plan Popularity & Performance (Exclude ₱0 spin prizes and unassigned plans)
    commercial_sessions = sessions_qs.filter(plan__isnull=False, amount_paid__gt=0).exclude(plan__name__startswith="Prize:")
    plan_stats = commercial_sessions.values('plan__name').annotate(
        count=Count('id'),
        total_revenue=Sum('amount_paid'),
        avg_duration=Avg('duration_minutes_purchased')
    ).order_by('-count')

    # Top plan
    top_plan = plan_stats[0]['plan__name'] if plan_stats else 'N/A'

    # Average session duration
    avg_duration = commercial_sessions.aggregate(avg=Avg('duration_minutes_purchased'))['avg'] or 0

    # Total sessions and unique devices in period
    total_sessions_count = sessions_qs.count()
    unique_devices = sessions_qs.values('mac_address').distinct().count()

    # Retention: devices with >1 session in period
    from django.db.models import Count as CountAgg
    returning_devices = sessions_qs.values('mac_address').annotate(
        sessions_count=CountAgg('id')
    ).filter(sessions_count__gt=1).count()
    retention_rate = round((returning_devices / unique_devices) * 100, 1) if unique_devices > 0 else 0

    # Avg revenue per session
    avg_rev_per_session = sessions_qs.aggregate(avg=Avg('amount_paid'))['avg'] or 0

    # Diagnostic: Peak Hour
    from django.db.models.functions import ExtractHour, ExtractWeekDay
    import zoneinfo
    tz_info = zoneinfo.ZoneInfo(settings.TIME_ZONE)
    peak_hour_data = sessions_qs.annotate(
        hour=ExtractHour('time_in', tzinfo=tz_info)
    ).values('hour').annotate(
        count=Count('id')
    ).order_by('-count').first()

    peak_hour_val = peak_hour_data['hour'] if peak_hour_data else None
    if peak_hour_val is not None:
        h = int(peak_hour_val)
        ampm = 'AM' if h < 12 else 'PM'
        display_h = h % 12 or 12
        peak_hour = f"{display_h}:00 {ampm}"
    else:
        peak_hour = 'N/A'

    # Diagnostic: Peak Day of Week
    day_names = ['', 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    peak_day_data = sessions_qs.annotate(
        weekday=ExtractWeekDay('time_in')
    ).values('weekday').annotate(
        count=Count('id')
    ).order_by('-count').first()
    peak_day = day_names[peak_day_data['weekday']] if peak_day_data else 'N/A'

    # Revenue Growth benchmark (this week vs last week)
    week_ago = today - timedelta(days=7)
    this_week_rev = Session.objects.filter(
        time_in__date__gte=week_ago,
        status__in=['active', 'expired', 'paused']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0
    last_week_rev = Session.objects.filter(
        time_in__date__gte=week_ago - timedelta(days=7),
        time_in__date__lt=week_ago,
        status__in=['active', 'expired', 'paused']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0
    if last_week_rev > 0:
        revenue_growth = round(((this_week_rev - last_week_rev) / last_week_rev) * 100, 1)
    else:
        revenue_growth = 100 if this_week_rev > 0 else 0

    # Prescriptive Insights
    insights = []
    if peak_hour_data and peak_hour != 'N/A':
        insights.append({
            'title': 'Optimize for Peak Hours',
            'text': f"Your busiest traffic occurs around {peak_hour}. Consider promoting special study bundles or off-peak rates to balance solar power and bandwidth demand.",
            'type': 'tip'
        })
    if revenue_growth < 0:
        insights.append({
            'title': 'Weekly Revenue Adjustment',
            'text': f"Revenue dipped {abs(revenue_growth)}% compared to last week. Consider introducing a new high-value plan or launching a promo announcement.",
            'type': 'warning'
        })
    elif revenue_growth > 15:
        insights.append({
            'title': 'Strong Growth Momentum',
            'text': f"Weekly revenue grew +{revenue_growth}% — excellent student adoption! Your current rate tiers are performing effectively.",
            'type': 'success'
        })
    if retention_rate > 0 and retention_rate < 35:
        insights.append({
            'title': 'Student Retention Opportunity',
            'text': f"{retention_rate}% of connected devices are returning users. Adding loyalty point rewards or daily streak bonuses can boost repeat visits.",
            'type': 'warning'
        })
    elif retention_rate >= 50:
        insights.append({
            'title': 'High Student Loyalty',
            'text': f"{retention_rate}% of devices return regularly, showing solid student retention and campus demand.",
            'type': 'success'
        })
    if top_plan != 'N/A' and plan_stats and len(plan_stats) > 1:
        top_pct = round((plan_stats[0]['count'] / total_sessions_count) * 100) if total_sessions_count > 0 else 0
        if top_pct > 65:
            insights.append({
                'title': 'Single Plan Dominance',
                'text': f"{top_plan} accounts for {top_pct}% of total purchases. Consider adjusting intermediate plan durations to encourage variety.",
                'type': 'info'
            })

    context = {
        'period': period,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
        'plan_stats': plan_stats,
        'avg_duration': round(avg_duration, 1),
        'top_plan': top_plan,
        'peak_hour': peak_hour,
        'peak_day': peak_day,
        'avg_rev_per_session': round(avg_rev_per_session, 1),
        'revenue_growth': revenue_growth,
        'total_sessions': total_sessions_count,
        'unique_devices': unique_devices,
        'retention_rate': retention_rate,
        'insights': insights,
        'active_page': 'analytics',
    }
    return render(request, 'dashboard/analytics.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def roi(request):
    """ROI tracker page with accurate financial computation."""
    from dashboard.models import OperatingExpense
    
    # Handle POST actions (add_cost, delete_cost, add_expense, delete_expense)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_cost':
            desc = request.POST.get('description', '').strip()
            amount = request.POST.get('amount', '').strip()
            if desc and amount:
                try:
                    ProjectCost.objects.create(description=desc, amount=int(amount))
                except (ValueError, TypeError):
                    pass
        elif action == 'delete_cost':
            cost_id = request.POST.get('cost_id')
            if cost_id:
                ProjectCost.objects.filter(id=cost_id).delete()
        elif action == 'add_expense':
            name = request.POST.get('name', '').strip()
            amount = request.POST.get('amount', '').strip()
            period = request.POST.get('period', 'monthly').strip()
            if name and amount:
                try:
                    OperatingExpense.objects.create(name=name, amount=int(amount), period=period)
                except (ValueError, TypeError):
                    pass
        elif action == 'delete_expense':
            expense_id = request.POST.get('expense_id')
            if expense_id:
                OperatingExpense.objects.filter(id=expense_id).delete()
        return redirect('dashboard:roi')

    # === INVESTMENT (one-time capital project costs) ===
    total_investment = ProjectCost.total_cost()
    costs = ProjectCost.objects.all()

    # === GROSS REVENUE (all coins ever inserted) ===
    gross_revenue = CoinEvent.objects.aggregate(
        total=Sum('amount')
    )['total'] or 0

    # === OPERATING DAYS ===
    first_session = Session.objects.order_by('time_in').first()
    first_coin = CoinEvent.objects.order_by('timestamp').first()
    first_dates = []
    if first_session and first_session.time_in:
        first_dates.append(first_session.time_in)
    if first_coin and first_coin.timestamp:
        first_dates.append(first_coin.timestamp)

    if first_dates:
        earliest_date = min(first_dates)
        days_operating = max((timezone.now() - earliest_date).days, 1)
    else:
        days_operating = 1

    operating_expenses = OperatingExpense.objects.all()
    total_expenses = OperatingExpense.calculate_total_expenses(days_operating)

    # === NET PROFIT ===
    net_profit = round(gross_revenue - total_expenses, 2)

    # === ROI COMPUTATION ===
    # ROI % = (Net Profit / Total Investment) * 100
    if total_investment > 0:
        roi_pct = round((net_profit / total_investment * 100), 1)
        recovery_progress = min(100, max(0, round((net_profit / total_investment * 100), 1)))
    else:
        roi_pct = 100.0 if net_profit > 0 else 0.0
        recovery_progress = 100.0 if net_profit > 0 else 0.0

    # === DAILY AVERAGES & BREAKEVEN FORECAST ===
    if days_operating > 0 and gross_revenue > 0:
        daily_avg_revenue = round(gross_revenue / days_operating, 2)
        daily_avg_expense = round(total_expenses / days_operating, 2)
        daily_avg_profit = round(net_profit / days_operating, 2)

        # Remaining capital to recover
        remaining_to_recover = max(0, total_investment - net_profit)

        if remaining_to_recover == 0:
            is_breakeven_reached = True
            days_to_breakeven = 0
            projected_date = timezone.localdate()
        elif daily_avg_profit > 0:
            is_breakeven_reached = False
            days_to_breakeven = int(remaining_to_recover / daily_avg_profit)
            if days_to_breakeven > 36500:  # Cap at 100 years
                days_to_breakeven = 36500
            projected_date = timezone.localdate() + timedelta(days=days_to_breakeven)
        else:
            is_breakeven_reached = False
            days_to_breakeven = 0
            projected_date = None
    else:
        daily_avg_revenue = 0
        daily_avg_expense = 0
        daily_avg_profit = 0
        days_to_breakeven = 0
        projected_date = None
        is_breakeven_reached = False
        remaining_to_recover = total_investment

    # Monthly projections (30-day baseline)
    monthly_revenue = round(daily_avg_revenue * 30, 2)
    monthly_expenses = round(daily_avg_expense * 30, 2)
    monthly_profit = round(daily_avg_profit * 30, 2)

    context = {
        'total_investment': total_investment,
        'costs': costs,
        'gross_revenue': gross_revenue,
        'total_expenses': total_expenses,
        'operating_expenses': operating_expenses,
        'net_profit': net_profit,
        'roi_percentage': roi_pct,
        'recovery_progress': recovery_progress,
        'remaining_to_recover': remaining_to_recover,
        'is_breakeven_reached': is_breakeven_reached,
        'daily_avg_revenue': daily_avg_revenue,
        'daily_avg_expense': daily_avg_expense,
        'daily_avg_profit': daily_avg_profit,
        'days_operating': days_operating,
        'monthly_revenue': monthly_revenue,
        'monthly_expenses': monthly_expenses,
        'monthly_profit': monthly_profit,
        'days_to_breakeven': days_to_breakeven,
        'projected_breakeven': projected_date,
        'active_page': 'roi',
    }
    return render(request, 'dashboard/roi.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def announcements_view(request):
    """Announcement management page."""
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            message = request.POST.get('message', '').strip()
            if message:
                Announcement.objects.create(message=message)
        elif action == 'update':
            ann_id = request.POST.get('announcement_id')
            message = request.POST.get('message', '').strip()
            if ann_id and message:
                Announcement.objects.filter(id=ann_id).update(message=message)
        elif action == 'toggle':
            ann_id = request.POST.get('announcement_id')
            try:
                ann = Announcement.objects.get(id=ann_id)
                ann.is_active = not ann.is_active
                ann.save()
            except Announcement.DoesNotExist:
                pass
        elif action == 'delete':
            ann_id = request.POST.get('announcement_id')
            Announcement.objects.filter(id=ann_id).delete()

        return redirect('dashboard:announcements')

    announcements = Announcement.objects.all()
    context = {
        'announcements': announcements,
        'active_page': 'announcements',
    }
    return render(request, 'dashboard/announcements.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def plans_view(request):
    """Dashboard-native WiFi rates management (Plan CRUD)."""
    error_message = ''

    if request.method == 'POST':
        action = request.POST.get('action')

        if action in ('create', 'update'):
            plan_id = request.POST.get('plan_id')
            name = request.POST.get('name', '').strip()
            price_raw = request.POST.get('price', '').strip()
            duration_raw = request.POST.get('duration_minutes', '').strip()
            speed_limit_raw = request.POST.get('speed_limit', '').strip()
            speed_limit_upload_raw = request.POST.get('speed_limit_upload', '').strip()
            pause_limit_raw = request.POST.get('pause_limit', '0').strip()
            pause_duration_limit_raw = request.POST.get('pause_duration_limit', '0').strip()
            is_active = request.POST.get('is_active') in ('on', 'true', '1')

            try:
                price = int(price_raw)
                duration_minutes = int(duration_raw)
                pause_limit = int(pause_limit_raw) if pause_limit_raw else 0
                pause_duration_limit = int(pause_duration_limit_raw) if pause_duration_limit_raw else 0
                
                if not name:
                    name = f"₱{price} Plan"
                    
                if price <= 0 or duration_minutes <= 0:
                    raise ValueError('Price and duration must be positive.')

                speed_limit = None
                if speed_limit_raw:
                    speed_limit = Decimal(speed_limit_raw)
                    if speed_limit <= 0:
                        raise ValueError('Speed limit must be positive when provided.')

                speed_limit_upload = None
                if speed_limit_upload_raw:
                    speed_limit_upload = Decimal(speed_limit_upload_raw)
                    if speed_limit_upload <= 0:
                        raise ValueError('Upload speed limit must be positive when provided.')

                if action == 'create':
                    Plan.objects.create(
                        name=name,
                        price=price,
                        duration_minutes=duration_minutes,
                        speed_limit=speed_limit,
                        speed_limit_upload=speed_limit_upload,
                        pause_limit=pause_limit,
                        pause_duration_limit=pause_duration_limit,
                        is_active=is_active,
                    )
                else:
                    plan = Plan.objects.filter(id=plan_id).first()
                    if plan:
                        plan.name = name
                        plan.price = price
                        plan.duration_minutes = duration_minutes
                        plan.speed_limit = speed_limit
                        plan.speed_limit_upload = speed_limit_upload
                        plan.pause_limit = pause_limit
                        plan.pause_duration_limit = pause_duration_limit
                        plan.is_active = is_active
                        plan.save()
            except (ValueError, InvalidOperation) as exc:
                error_message = str(exc)

        elif action == 'delete':
            plan_id = request.POST.get('plan_id')
            plan = Plan.objects.filter(id=plan_id).first()
            if not plan:
                error_message = 'Plan not found.'
            else:
                active_sessions = plan.sessions.filter(status='active').count()
                active_groups = plan.session_groups.filter(status='active').count()
                
                if active_sessions > 0 or active_groups > 0:
                    error_message = f'Cannot delete this plan. It is currently used by {active_sessions} active sessions and {active_groups} active group passes. Set it inactive instead.'
                else:
                    try:
                        plan.delete()
                    except ProtectedError:
                        error_message = 'Cannot delete this plan because it is restricted by the database. Set it inactive instead.'
        if not error_message:
            return redirect('dashboard:plans')

    context = {
        'plans': Plan.objects.filter(price__gt=0).order_by('price', 'id'),
        'active_page': 'plans',
        'error_message': error_message,
    }
    return render(request, 'dashboard/plans.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def security_view(request):
    """Suspicious device monitoring and enforcement actions."""
    status_filter = request.GET.get('status', '').strip()
    search = request.GET.get('search', '').strip()
    action_message = ''
    action_error = ''

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        incident_id = request.POST.get('incident_id', '').strip()
        incident = SuspiciousDevice.objects.filter(id=incident_id).first()

        if not incident:
            action_error = 'Suspicious device record not found.'
        elif action == 'block':
            blocked = iptables.block_device(incident.mac_address)
            if blocked:
                incident.mark_blocked(by=request.user.username)
                action_message = f'Device {incident.mac_address} blocked successfully.'
                audit_logger.info(
                    'event=suspicious_device_blocked user=%s mac=%s ip=%s',
                    request.user.username,
                    incident.mac_address,
                    _client_ip(request),
                )
            else:
                action_error = 'Failed to block device at firewall layer.'
        elif action == 'unblock':
            allowed = iptables.allow_device(incident.mac_address)
            if allowed:
                incident.mark_cleared(by=request.user.username)
                action_message = f'Device {incident.mac_address} unblocked and marked as cleared.'
                audit_logger.info(
                    'event=suspicious_device_unblocked user=%s mac=%s ip=%s',
                    request.user.username,
                    incident.mac_address,
                    _client_ip(request),
                )
            else:
                action_error = 'Failed to re-allow device at firewall layer.'
        elif action == 'false_positive':
            incident.mark_false_positive(by=request.user.username)
            action_message = f'Device {incident.mac_address} marked as false positive.'
        elif action == 'clear':
            incident.mark_cleared(by=request.user.username)
            action_message = f'Device {incident.mac_address} marked as cleared.'
        else:
            action_error = 'Unsupported action.'

    suspicious_devices = SuspiciousDevice.objects.all()

    if status_filter:
        suspicious_devices = suspicious_devices.filter(status=status_filter)

    if search:
        suspicious_devices = suspicious_devices.filter(
            Q(mac_address__icontains=search)
            | Q(last_ip_address__icontains=search)
            | Q(reason__icontains=search)
            | Q(evidence__icontains=search)
        )

    counts = {
        'new': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_NEW).count(),
        'blocked': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_BLOCKED).count(),
        'false_positive': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_FALSE_POSITIVE).count(),
        'cleared': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_CLEARED).count(),
    }

    context = {
        'active_page': 'security',
        'suspicious_devices': suspicious_devices,
        'status_filter': status_filter,
        'search': search,
        'status_choices': SuspiciousDevice.STATUS_CHOICES,
        'counts': counts,
        'action_message': action_message,
        'action_error': action_error,
    }
    return render(request, 'dashboard/security.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def account_view(request):
    """Dashboard account settings for admin email, password, and multi-admin management."""
    if not request.user.is_authenticated:
        return redirect(f'/admin/login/?next={request.path}')

    from django.contrib.auth import get_user_model
    User = get_user_model()
    email_message = ''
    email_error = ''
    password_message = ''
    admin_message = ''
    admin_error = ''
    password_form = PasswordChangeForm(request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_email':
            email = request.POST.get('email', '').strip()
            if email:
                request.user.email = email
                request.user.save(update_fields=['email'])
                email_message = 'Email updated successfully.'
                audit_logger.info("event=admin_email_updated user=%s email=%s", request.user.username, email)
            else:
                email_error = 'Email cannot be empty.'

        elif action == 'change_password':
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                password_message = 'Password updated successfully.'
                audit_logger.info("event=admin_password_updated user=%s", request.user.username)
                password_form = PasswordChangeForm(request.user)

        elif action == 'create_admin':
            new_username = request.POST.get('new_username', '').strip()
            new_email = request.POST.get('new_email', '').strip()
            new_password = request.POST.get('new_password', '').strip()
            new_role = request.POST.get('new_role', 'staff')

            if not new_username:
                admin_error = 'Username cannot be empty.'
            elif len(new_password) < 6:
                admin_error = 'Password must be at least 6 characters long.'
            elif User.objects.filter(username__iexact=new_username).exists():
                admin_error = f'Username "{new_username}" is already taken.'
            else:
                is_superuser = (new_role == 'superuser')
                new_user = User.objects.create_user(
                    username=new_username,
                    email=new_email,
                    password=new_password,
                    is_staff=True,
                    is_superuser=is_superuser,
                )
                admin_message = f'Admin account "{new_username}" created successfully.'
                audit_logger.info(
                    "event=admin_created creator=%s created_user=%s is_superuser=%s",
                    request.user.username, new_username, is_superuser
                )

        elif action == 'delete_admin':
            target_id = request.POST.get('target_user_id')
            try:
                target_user = User.objects.get(id=target_id, is_staff=True)
                if target_user.id == request.user.id:
                    admin_error = 'You cannot delete your own active administrator account.'
                elif target_user.is_superuser and User.objects.filter(is_superuser=True, is_staff=True).count() <= 1:
                    admin_error = 'Cannot delete the only remaining Superadmin account.'
                else:
                    deleted_name = target_user.username
                    target_user.delete()
                    admin_message = f'Admin account "{deleted_name}" deleted successfully.'
                    audit_logger.info(
                        "event=admin_deleted by=%s deleted_user=%s",
                        request.user.username, deleted_name
                    )
            except User.DoesNotExist:
                admin_error = 'User not found.'

    admin_users = User.objects.filter(is_staff=True).order_by('-is_superuser', 'username')

    context = {
        'active_page': 'account',
        'email_message': email_message,
        'email_error': email_error,
        'password_message': password_message,
        'admin_message': admin_message,
        'admin_error': admin_error,
        'password_form': password_form,
        'admin_users': admin_users,
    }
    return render(request, 'dashboard/account.html', context)

@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def settings_view(request):
    """View to manage global system settings."""
    settings_obj = SystemSettings.get_settings()
    message = None
    error_message = None

    if request.method == 'POST':
        try:
            # Networking
            settings_obj.enable_anti_tethering = request.POST.get('enable_anti_tethering') == 'on'
            settings_obj.enable_sqm = request.POST.get('enable_sqm') == 'on'
            settings_obj.isp_download_speed = int(request.POST.get('isp_download_speed', 100))
            settings_obj.isp_upload_speed = int(request.POST.get('isp_upload_speed', 100))
            
            # General / UI
            settings_obj.enable_dark_mode = request.POST.get('enable_dark_mode') == 'on'
            settings_obj.max_concurrent_sessions = int(request.POST.get('max_concurrent_sessions', 20))
            settings_obj.global_pause_limit_hours = max(1, int(request.POST.get('global_pause_limit_hours', 24)))
            
            # Network & Automation Features
            settings_obj.enable_internet_check = request.POST.get('enable_internet_check') == 'on'
            settings_obj.enable_auto_pause_resume = request.POST.get('enable_auto_pause_resume') == 'on'
            settings_obj.auto_pause_timeout_seconds = int(request.POST.get('auto_pause_timeout_seconds', 300))
            settings_obj.insert_coin_countdown_seconds = int(request.POST.get('insert_coin_countdown_seconds', 120))
            
            # Gamification
            settings_obj.enable_spin_wheel = request.POST.get('enable_spin_wheel') == 'on'
            settings_obj.spin_cost_points = int(request.POST.get('spin_cost_points', 10))
            settings_obj.daily_spin_limit = int(request.POST.get('daily_spin_limit', 3))
            settings_obj.points_per_streak_day = int(request.POST.get('points_per_streak_day', 5))
            
            # Family Pass
            if 'enable_family_pass' in request.POST or 'family_pass_base_rate' in request.POST:
                settings_obj.enable_family_pass = request.POST.get('enable_family_pass') == 'on'
                if request.POST.get('family_pass_base_rate'):
                    settings_obj.family_pass_base_rate = int(request.POST.get('family_pass_base_rate', 20))
                if request.POST.get('family_pass_device_rate'):
                    settings_obj.family_pass_device_rate = int(request.POST.get('family_pass_device_rate', 5))
                if request.POST.get('family_pass_max_devices'):
                    settings_obj.family_pass_max_devices = int(request.POST.get('family_pass_max_devices', 6))
                if request.POST.get('family_pass_speed_limit'):
                    settings_obj.family_pass_speed_limit = float(request.POST.get('family_pass_speed_limit', 5.0))
                if request.POST.get('family_pass_speed_limit_upload'):
                    settings_obj.family_pass_speed_limit_upload = float(request.POST.get('family_pass_speed_limit_upload', 5.0))
            if 'group_code_expiry_hours' in request.POST:
                settings_obj.group_code_expiry_hours = max(0, int(request.POST.get('group_code_expiry_hours', 24)))
            
            settings_obj.save()
            message = "Settings updated successfully."
            
            # Apply network settings immediately if on Linux
            try:
                from sessions_app.iptables import apply_network_settings
                apply_network_settings()
            except Exception as e:
                logging.error(f"Failed to apply network settings: {e}")
                message += " (Note: Network rules could not be applied, please check logs)."

        except ValueError:
            error_message = "Invalid input for numeric fields."
        except Exception as e:
            error_message = f"An error occurred: {e}"

    context = {
        'active_page': 'settings',
        'settings': settings_obj,
        'message': message,
        'error_message': error_message,
    }
    return render(request, 'dashboard/settings.html', context)


from django.views.decorators.http import require_POST

@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
@require_POST
def admin_pause_all_sessions(request):
    """Admin endpoint to pause all currently active sessions at once."""
    from django.http import JsonResponse
    from django.utils import timezone
    from sessions_app.models import Session

    active_sessions = Session.objects.filter(status='active')
    count = active_sessions.count()
    if count == 0:
        return JsonResponse({'success': False, 'error': 'No active sessions to pause.'})

    now = timezone.now()
    for session in active_sessions:
        session.status = "paused"
        session.paused_at = now
        session.pause_count += 1
        session.save(update_fields=["status", "paused_at", "pause_count"])
        try:
            from sessions_app.iptables import block_device
            block_device(session.mac_address)
        except Exception as e:
            logging.error(f"Failed to block device {session.mac_address} on bulk pause: {e}")

    audit_logger.info(
        "event=pause_all_sessions user=%s count=%d ip=%s",
        request.user.username, count, _client_ip(request)
    )

    return JsonResponse({
        'success': True,
        'paused_count': count,
        'message': f'Successfully paused {count} active session(s).'
    })


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
@require_POST
def admin_session_action(request, session_id, action):
    """Admin endpoint to pause, resume, edit, or delete a session from the dashboard."""
    import json
    from django.http import JsonResponse
    from django.utils import timezone
    from sessions_app.models import Session
    
    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Session not found'}, status=404)
        
    if action == 'pause':
        if session.status != 'active':
            return JsonResponse({'success': False, 'error': 'Session is not active'})
        if session.plan and session.plan.pause_duration_limit > 0 and session.pause_count >= session.plan.pause_duration_limit:
            # Using pause_duration_limit loosely here as a limit or maybe no limit if it's not strictly tracked by count.
            pass
            
        session.status = "paused"
        session.paused_at = timezone.now()
        session.pause_count += 1
        session.save(update_fields=["status", "paused_at", "pause_count"])
        
        # Remove from firewall
        try:
            from sessions_app.iptables import block_device
            block_device(session.mac_address)
        except Exception as e:
            logging.error(f"Failed to block device on pause: {e}")
            
    elif action == 'resume':
        if session.status != 'paused':
            return JsonResponse({'success': False, 'error': 'Session is not paused'})
            
        if session.paused_at:
            paused_seconds = (timezone.now() - session.paused_at).total_seconds()
            session.total_paused_seconds += paused_seconds
            
        session.status = "active"
        session.paused_at = None
        session.save(update_fields=["status", "total_paused_seconds", "paused_at"])
        
        # Allow in firewall
        try:
            from sessions_app.iptables import allow_device
            dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
            ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
            allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
        except Exception as e:
            logging.error(f"Failed to allow device on resume: {e}")
            
    elif action in ('delete', 'end', 'disconnect'):
        if session.status in ('active', 'paused'):
            try:
                from sessions_app.iptables import block_device
                block_device(session.mac_address)
            except Exception as e:
                logging.error(f"Failed to block device on disconnect: {e}")
        session.status = 'expired'
        session.time_out = timezone.now()
        session.save(update_fields=['status', 'time_out'])

    elif action == 'block':
        from sessions_app.models import SuspiciousDevice
        try:
            from sessions_app.iptables import block_device
            block_device(session.mac_address)
        except Exception as e:
            logging.error(f"Failed to block device in iptables: {e}")

        # Mark any active session as expired
        if session.status in ('active', 'paused'):
            session.status = 'expired'
            session.time_out = timezone.now()
            session.save(update_fields=['status', 'time_out'])

        # Add or update Blacklist / SuspiciousDevice entry
        susp, created = SuspiciousDevice.objects.get_or_create(
            mac_address=session.mac_address,
            defaults={
                'last_ip_address': session.ip_address or '',
                'reason': 'Manual Admin Block',
                'evidence': f'Blocked by administrator from Users table (Session #{session.id})',
                'status': SuspiciousDevice.STATUS_BLOCKED,
                'is_blocked': True,
                'blocked_at': timezone.now(),
                'resolved_by': getattr(request.user, 'username', 'admin')
            }
        )
        if not created:
            susp.status = SuspiciousDevice.STATUS_BLOCKED
            susp.is_blocked = True
            susp.blocked_at = timezone.now()
            susp.resolved_by = getattr(request.user, 'username', 'admin')
            susp.save()

    elif action == 'edit':
        try:
            data = json.loads(request.body)
            new_name = data.get('device_name')
            if new_name is not None:
                session.device_name = new_name
                session.save(update_fields=['device_name'])
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
            
    else:
        return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)
        
    return JsonResponse({'success': True})

@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def logs_view(request):
    """View for system logs and coin events."""
    from sessions_app.models import CoinEvent
    from django.conf import settings
    import os

    search_mac = request.GET.get('mac', '').strip().upper()

    coin_events_qs = CoinEvent.objects.all().order_by('-timestamp')
    if search_mac:
        coin_events_qs = coin_events_qs.filter(mac_address__icontains=search_mac)

    # Pagination for Coin Events
    page = request.GET.get('page', 1)
    paginator = Paginator(coin_events_qs, 100)
    try:
        coin_events = paginator.page(page)
    except PageNotAnInteger:
        coin_events = paginator.page(1)
    except EmptyPage:
        coin_events = paginator.page(paginator.num_pages)

    # Read audit.log
    audit_log_lines = []
    log_path = os.path.join(settings.BASE_DIR, 'logs', 'audit.log')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                # Read last 500 lines
                lines = f.readlines()
                audit_log_lines = lines[-500:] if len(lines) > 500 else lines
                audit_log_lines.reverse()  # Newest first
        except Exception as e:
            audit_log_lines = [f'Error reading log file: {e}']
    else:
        audit_log_lines = ['Audit log file not found.']

    context = {
        'active_page': 'logs',
        'coin_events': coin_events,
        'audit_log_lines': audit_log_lines,
        'search_mac': search_mac,
    }
    return render(request, 'dashboard/logs.html', context)




@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def gamification_view(request):
    from dashboard.models import SystemSettings
    from sessions_app.models import SpinPrize
    
    settings_obj = SystemSettings.get_settings()
    
    if request.method == "POST":
        action = request.POST.get("action")
        
        if action == "update_settings":
            settings_obj.enable_spin_wheel = request.POST.get("enable_spin_wheel") == "on"
            try:
                settings_obj.spin_cost_points = int(request.POST.get("spin_cost_points", 10))
                settings_obj.points_per_streak_day = int(request.POST.get("points_per_streak_day", 5))
                settings_obj.points_per_peso = int(request.POST.get("points_per_peso", 1))
                settings_obj.save()
                messages.success(request, "Gamification point rules updated successfully.")
            except ValueError:
                messages.error(request, "Invalid input for settings. Values must be numbers.")
                
        elif action == "add_prize" or action == "edit_prize":
            try:
                prize_id = request.POST.get("prize_id")
                name = request.POST.get("name", "").strip()
                minutes = int(request.POST.get("minutes_reward", 0))
                weight = int(request.POST.get("probability_weight", 10))
                is_active = request.POST.get("is_active") == "on"
                
                speed_limit = request.POST.get("speed_limit")
                speed_limit = float(speed_limit) if speed_limit else None
                
                speed_limit_upload = request.POST.get("speed_limit_upload")
                speed_limit_upload = float(speed_limit_upload) if speed_limit_upload else None
                
                pause_limit = int(request.POST.get("pause_limit") or 0)
                pause_duration_limit = int(request.POST.get("pause_duration_limit") or 0)
                
                if action == "edit_prize" and prize_id:
                    prize = SpinPrize.objects.get(id=prize_id)
                    prize.name = name
                    prize.minutes_reward = minutes
                    prize.probability_weight = weight
                    prize.is_active = is_active
                    prize.speed_limit = speed_limit
                    prize.speed_limit_upload = speed_limit_upload
                    prize.pause_limit = pause_limit
                    prize.pause_duration_limit = pause_duration_limit
                    prize.save()
                    messages.success(request, f'Prize "{name}" updated successfully.')
                else:
                    SpinPrize.objects.create(
                        name=name,
                        minutes_reward=minutes,
                        probability_weight=weight,
                        is_active=is_active,
                        speed_limit=speed_limit,
                        speed_limit_upload=speed_limit_upload,
                        pause_limit=pause_limit,
                        pause_duration_limit=pause_duration_limit
                    )
                    messages.success(request, f'New prize "{name}" added to wheel.')
            except ValueError:
                messages.error(request, "Invalid input for prize parameters.")
            except SpinPrize.DoesNotExist:
                messages.error(request, "Prize record not found.")
                
        return redirect("dashboard:gamification")

    prizes = list(SpinPrize.objects.all().order_by("-is_active", "-probability_weight"))
    active_prizes_count = sum(1 for p in prizes if p.is_active)
    total_prizes_count = len(prizes)
    total_active_weight = sum(p.probability_weight for p in prizes if p.is_active) or 1

    for p in prizes:
        if p.is_active:
            p.chance_pct = round((p.probability_weight / total_active_weight) * 100, 1)
        else:
            p.chance_pct = 0.0
    
    context = {
        "active_page": "gamification",
        "settings": settings_obj,
        "prizes": prizes,
        "active_prizes_count": active_prizes_count,
        "total_prizes_count": total_prizes_count,
        "total_active_weight": total_active_weight,
    }
    return render(request, "dashboard/gamification.html", context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def delete_prize_view(request, prize_id):
    from sessions_app.models import SpinPrize
    if request.method == 'POST':
        try:
            prize = SpinPrize.objects.get(id=prize_id)
            prize.delete()
            messages.success(request, 'Prize deleted successfully.')
        except SpinPrize.DoesNotExist:
            messages.error(request, 'Prize not found.')
            
    return redirect('dashboard:gamification')
import os
from django.conf import settings
from django.http import FileResponse, Http404

@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def backup_database(request):
    import io
    import sqlite3
    from pathlib import Path
    from django.core.management import call_command
    from django.http import HttpResponse

    # 1. Check configured database name
    db_config = settings.DATABASES.get('default', {})
    db_name = db_config.get('NAME')
    
    candidate_paths = []
    if db_name:
        candidate_paths.append(Path(db_name))
    candidate_paths.extend([
        Path(settings.BASE_DIR) / 'db.sqlite3',
        Path(settings.BASE_DIR) / 'pisowifi' / 'db.sqlite3',
        Path(settings.BASE_DIR).parent / 'db.sqlite3',
        Path('/opt/iconnect/pisowifi/db.sqlite3'),
        Path('/opt/iconnect/db.sqlite3'),
    ])

    found_path = None
    for p in candidate_paths:
        try:
            if p and p.is_file() and p.stat().st_size > 0:
                found_path = p
                break
        except Exception:
            continue

    if found_path:
        try:
            with open(found_path, 'rb') as f:
                content = f.read()

            response = HttpResponse(content, content_type='application/x-sqlite3')
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            response['Content-Disposition'] = f'attachment; filename="iconnect_backup_{timestamp}.sqlite3"'
            audit_logger.info("event=database_backup_download user=%s size=%d", request.user.username, len(content))
            return response
        except Exception as e:
            logger.error(f"Error reading SQLite backup file: {e}")

    # Fallback: Django JSON dumpdata backup
    try:
        buffer = io.StringIO()
        call_command('dumpdata', stdout=buffer, exclude=['contenttypes', 'auth.permission'])
        json_data = buffer.getvalue().encode('utf-8')
        response = HttpResponse(json_data, content_type='application/json')
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        response['Content-Disposition'] = f'attachment; filename="iconnect_backup_{timestamp}.json"'
        audit_logger.info("event=database_backup_dumpdata user=%s size=%d", request.user.username, len(json_data))
        return response
    except Exception as e:
        logger.error(f"Dumpdata backup failed: {e}")
        raise Http404("Database backup could not be generated.")


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def issues_view(request):
    """View and manage customer issue tickets and operator messages."""
    status_filter = request.GET.get('status', 'all')
    category_filter = request.GET.get('category', 'all')
    search_query = request.GET.get('q', '').strip()

    reports = IssueReport.objects.all()

    if status_filter in ['pending', 'resolved']:
        reports = reports.filter(status=status_filter)

    if category_filter in dict(IssueReport.CATEGORY_CHOICES).keys():
        reports = reports.filter(category=category_filter)

    if search_query:
        reports = reports.filter(
            Q(mac_address__icontains=search_query) |
            Q(contact_info__icontains=search_query) |
            Q(message__icontains=search_query)
        )

    # Stats
    total_count = IssueReport.objects.count()
    pending_count = IssueReport.objects.filter(status='pending').count()
    resolved_count = IssueReport.objects.filter(status='resolved').count()

    paginator = Paginator(reports, 15)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'active_page': 'issues',
        'reports': page_obj,
        'status_filter': status_filter,
        'category_filter': category_filter,
        'search_query': search_query,
        'total_count': total_count,
        'pending_count': pending_count,
        'resolved_count': resolved_count,
        'categories': IssueReport.CATEGORY_CHOICES,
    }
    return render(request, 'dashboard/issues.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
@require_POST
def update_issue_status(request, issue_id):
    """Update issue status or operator notes."""
    issue = get_object_or_404(IssueReport, id=issue_id)
    new_status = request.POST.get('status')
    admin_notes = request.POST.get('admin_notes')

    if new_status in ['pending', 'resolved']:
        issue.status = new_status
        if new_status == 'resolved':
            issue.resolved_at = timezone.now()
        else:
            issue.resolved_at = None

    if admin_notes is not None:
        issue.admin_notes = admin_notes.strip()

    issue.save()
    messages.success(request, f'Ticket #{issue.id} updated.')
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard:issues')


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
@require_POST
def delete_issue(request, issue_id):
    """Delete an issue ticket."""
    issue = get_object_or_404(IssueReport, id=issue_id)
    issue_num = issue.id
    issue.delete()
    messages.success(request, f'Ticket #{issue_num} deleted.')
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard:issues')


