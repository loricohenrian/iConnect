"""
Dashboard Views — API endpoints and template views for admin dashboard
"""
import csv
import re
from datetime import timedelta, date
from decimal import Decimal, InvalidOperation
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
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
from .validators import validate_password_strength, validate_username, sanitize_text, parse_bounded_int, parse_bounded_float
from django.core.validators import validate_email
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


def _safe_redirect_referer(request, fallback='dashboard:overview'):
    """Redirect to referer safely only if it stays within the current host."""
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(url=referer, allowed_hosts={request.get_host()}):
        return redirect(referer)
    return redirect(fallback)


def dashboard_login(request):
    """Dashboard login page."""
    if _is_dashboard_admin(request.user):
        return redirect('dashboard:overview')

    error_message = ''
    default_next = reverse('dashboard:overview')
    requested_next = request.GET.get('next') or request.POST.get('next') or default_next
    if url_has_allowed_host_and_scheme(
        requested_next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = requested_next
    else:
        next_url = default_next

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
        if not user:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                user_by_email = User.objects.filter(email__iexact=username, is_active=True).first()
                if user_by_email and user_by_email.check_password(password):
                    user = user_by_email
            except Exception as e:
                logger.warning('email_login_lookup_error: %s', e)

        if user and user.is_staff:
            try:
                cache.delete(lock_key)
            except Exception as exc:
                logger.warning('dashboard_login_lock_clear_cache_unavailable key=%s error=%s', lock_key, exc)
            login(request, user)
            audit_logger.info('event=dashboard_login_success user=%s ip=%s', user.username, ip)
            return redirect(next_url)
        audit_logger.warning('event=dashboard_login_failed username=%s ip=%s', username, ip)
        error_message = 'Invalid username/email or password.'

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
        announcements = Announcement.objects.filter(is_active=True).exclude(message__contains="interrupted by our ISP")
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

    from sessions_app.tasks import cleanup_expired_and_stale_sessions
    cleanup_expired_and_stale_sessions()

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
    from sessions_app.views import _get_dhcp_hostname
    for s in recent_qs:
        dev_name = s.device_name or 'Unknown'
        if dev_name in ('Unknown', 'Android Phone', 'Android', 'User Device', 'K'):
            dhcp_name = _get_dhcp_hostname(s.mac_address)
            if dhcp_name:
                dev_name = dhcp_name
                Session.objects.filter(id=s.id).update(device_name=dhcp_name)
        recent_sessions_data.append({
            'id': s.id,
            'device_name': dev_name,
            'mac_address': s.mac_address,
            'plan_name': s.plan.name if s.plan else 'Custom',
            'amount_paid': str(s.amount_paid),
            'time_in': timezone.localtime(s.time_in).strftime('%b %d, %I:%M %p') if s.time_in else '-',
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

    week_ago = timezone.localdate() - timedelta(days=6)

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

    custom_start = request.query_params.get('start_date')
    custom_end = request.query_params.get('end_date')

    if custom_start:
        from django.utils.dateparse import parse_date
        start_date = parse_date(custom_start)
    elif period in ('daily', 'today'):
        start_date = today
    elif period in ('weekly', 'week'):
        start_date = today - timedelta(days=7)
    elif period in ('monthly', 'month'):
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
            'time_in_date': timezone.localtime(s.time_in).strftime('%b %d, %Y') if s.time_in else '-',
            'time_in_time': timezone.localtime(s.time_in).strftime('%I:%M %p') if s.time_in else '-',
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

    from sessions_app.tasks import cleanup_expired_and_stale_sessions
    cleanup_expired_and_stale_sessions()

    status_filter = request.GET.get('status', '')
    search = sanitize_text(request.GET.get('search', ''), max_length=60)
    period = request.GET.get('period', 'today')

    sessions = Session.objects.select_related('plan').all()

    now = timezone.now()
    today = timezone.localdate()
    if period == 'today' or not period:
        sessions = sessions.filter(
            Q(status='active') |
            Q(time_in__date=today) |
            Q(status='paused', paused_at__date=today) |
            Q(status='expired', time_out__date=today)
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
    from sessions_app.views import _get_dhcp_hostname
    for s in sessions[:100]:
        dev_name = s.device_name or 'Unknown'
        if dev_name in ('Unknown', 'Android Phone', 'Android', 'User Device', 'K'):
            dhcp_name = _get_dhcp_hostname(s.mac_address)
            if dhcp_name:
                dev_name = dhcp_name
                Session.objects.filter(id=s.id).update(device_name=dhcp_name)
        session_list.append({
            'id': s.id,
            'device_name': dev_name,
            'mac_address': s.mac_address,
            'ip_address': s.ip_address or '—',
            'plan_name': s.plan.name if s.plan else 'Custom',
            'plan_id': s.plan_id,
            'speed_limit': float(s.plan.speed_limit) if (s.plan and s.plan.speed_limit) else None,
            'speed_limit_upload': float(s.plan.speed_limit_upload) if (s.plan and s.plan.speed_limit_upload) else None,
            'amount_paid': str(s.amount_paid),
            'time_in': timezone.localtime(s.time_in).strftime('%b %d, %I:%M %p') if s.time_in else '—',
            'time_out': timezone.localtime(s.time_out).strftime('%b %d, %I:%M %p') if s.time_out else '—',
            'duration_minutes_purchased': s.duration_minutes_purchased,
            'is_ended_early': s.is_ended_early,
            'actual_elapsed_minutes': s.actual_elapsed_minutes,
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
        elif action == 'update_goal':
            daily_target = request.POST.get('daily_target', '').strip()
            weekly_target = request.POST.get('weekly_target', '').strip()
            if daily_target != '':
                try:
                    d_amt = max(0, int(daily_target))
                    RevenueGoal.objects.update_or_create(period='daily', defaults={'target_amount': d_amt})
                except (ValueError, TypeError):
                    pass
            if weekly_target != '':
                try:
                    w_amt = max(0, int(weekly_target))
                    RevenueGoal.objects.update_or_create(period='weekly', defaults={'target_amount': w_amt})
                except (ValueError, TypeError):
                    pass
            messages.success(request, "Revenue goals updated successfully.")
            return redirect(request.get_full_path())

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

    # Revenue Goals tracking
    daily_goal = RevenueGoal.objects.filter(period='daily').first()
    weekly_goal = RevenueGoal.objects.filter(period='weekly').first()
    today_sales_for_goal = CoinEvent.objects.filter(timestamp__date=today).aggregate(total=Sum('amount'))['total'] or 0
    week_start_for_goal = today - timedelta(days=today.weekday())
    week_sales_for_goal = CoinEvent.objects.filter(timestamp__date__gte=week_start_for_goal).aggregate(total=Sum('amount'))['total'] or 0
    daily_target_amt = daily_goal.target_amount if daily_goal else 0
    weekly_target_amt = weekly_goal.target_amount if weekly_goal else 0
    daily_progress = min(100, round((today_sales_for_goal / daily_target_amt) * 100)) if daily_target_amt > 0 else 0
    weekly_progress = min(100, round((week_sales_for_goal / weekly_target_amt) * 100)) if weekly_target_amt > 0 else 0

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
        'daily_target_amt': daily_target_amt,
        'weekly_target_amt': weekly_target_amt,
        'today_sales_for_goal': today_sales_for_goal,
        'week_sales_for_goal': week_sales_for_goal,
        'daily_progress': daily_progress,
        'weekly_progress': weekly_progress,
    }
    return render(request, 'dashboard/revenue.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def sessions_view(request):
    """Session logs page."""
    from sessions_app.tasks import cleanup_expired_and_stale_sessions
    cleanup_expired_and_stale_sessions()

    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')
    period = request.GET.get('period', 'today')

    sessions = Session.objects.select_related('plan').all()

    # Time period filter (default: today)
    now = timezone.now()
    today = timezone.localdate()
    if period == 'today' or not period:
        # Smart Today Filter: active sessions (online right now) + any session with activity today (started, paused, or ended today)
        sessions = sessions.filter(
            Q(status='active') |
            Q(time_in__date=today) |
            Q(status='paused', paused_at__date=today) |
            Q(status='expired', time_out__date=today)
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

    # Pagination: 25 sessions per page
    page = request.GET.get('page', 1)
    paginator = Paginator(sessions, 25)
    try:
        sessions_page = paginator.page(page)
    except PageNotAnInteger:
        sessions_page = paginator.page(1)
    except EmptyPage:
        sessions_page = paginator.page(paginator.num_pages)

    from sessions_app.views import _get_dhcp_hostname
    for s in sessions_page:
        if s.device_name in (None, '', 'Unknown', 'Android Phone', 'Android', 'User Device', 'K'):
            dhcp_name = _get_dhcp_hostname(s.mac_address)
            if dhcp_name:
                s.device_name = dhcp_name
                Session.objects.filter(id=s.id).update(device_name=dhcp_name)

    active_plans = Plan.objects.filter(is_active=True).order_by('price')

    context = {
        'sessions': sessions_page,
        'status_filter': status_filter,
        'search': search,
        'period': period,
        'active_page': 'sessions',
        'total_users': total_users,
        'connected_users': connected_users,
        'paused_users': paused_users,
        'disconnected_users': disconnected_users,
        'suspicious_macs': suspicious_macs,
        'active_plans': active_plans,
    }
    return render(request, 'dashboard/sessions.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def export_sessions_csv(request):
    """Export session logs as CSV file."""
    status_filter = request.GET.get('status', '')
    search = sanitize_text(request.GET.get('search', ''), max_length=60)
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

    # === Operating Expenses & Capital Costs ===
    first_session = Session.objects.order_by('time_in').first()
    first_coin = CoinEvent.objects.order_by('timestamp').first()
    from dashboard.models import OperatingExpense, ProjectCost
    first_cost = ProjectCost.objects.order_by('date_added').first()

    first_dates = []
    if first_cost and first_cost.date_added:
        first_dates.append(first_cost.date_added)
    if first_session and first_session.time_in:
        first_dates.append(first_session.time_in)
    if first_coin and first_coin.timestamp:
        first_dates.append(first_coin.timestamp)

    if first_dates:
        earliest_date = min(first_dates)
        days_operating = max((timezone.now() - earliest_date).days + 1, 1)
    else:
        days_operating = 1

    total_expenses = OperatingExpense.calculate_total_expenses(days_operating)
    operating_expenses = OperatingExpense.objects.all().order_by('-date_added')
    capital_costs = ProjectCost.objects.all().order_by('-date_added')

    # Itemized expenses list matching ROI tracker
    itemized_expenses = []
    for exp in operating_expenses:
        itemized_expenses.append({
            'name': exp.name,
            'frequency': exp.get_period_display(),
            'amount': exp.amount,
            'date': exp.date_added,
            'type': 'recurring',
        })
    for cost in capital_costs:
        itemized_expenses.append({
            'name': cost.description,
            'frequency': 'One-time',
            'amount': cost.amount,
            'date': cost.date_added,
            'type': 'capital',
        })

    # === Financial Summary ===
    total_investment = ProjectCost.total_cost()
    net_profit = round(revenue_all_time - total_expenses, 2)
    roi_pct = round((net_profit / total_investment * 100), 1) if total_investment > 0 else 0

    # === Top Plans (Excluding ₱0 prizes) with % of Total ===
    month_plan_qs = Session.objects.filter(
        time_in__date__gte=month_ago,
        status__in=['active', 'expired', 'paused'],
        amount_paid__gt=0,
        plan__isnull=False
    ).exclude(plan__name__startswith="Prize:")

    total_month_plan_rev = month_plan_qs.aggregate(total=Sum('amount_paid'))['total'] or 0
    total_month_plan_count = month_plan_qs.count()

    top_plans_raw = month_plan_qs.values('plan__name', 'plan__price').annotate(
        count=Count('id'),
        total=Sum('amount_paid'),
    ).order_by('-count')[:5]

    top_plans = []
    for p in top_plans_raw:
        plan_rev = float(p['total'] or 0)
        plan_count = int(p['count'] or 0)
        pct_rev = round((plan_rev / total_month_plan_rev) * 100, 1) if total_month_plan_rev > 0 else 0.0
        pct_cnt = round((plan_count / total_month_plan_count) * 100, 1) if total_month_plan_count > 0 else 0.0
        top_plans.append({
            'plan__name': p['plan__name'],
            'plan__price': p['plan__price'],
            'count': plan_count,
            'total': plan_rev,
            'pct_revenue': pct_rev,
            'pct_count': pct_cnt,
        })

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
        'itemized_expenses': itemized_expenses,
        'net_profit': net_profit,
        'total_investment': total_investment,
        'roi_percentage': roi_pct,
        'days_operating': days_operating,
        # Data
        'top_plans': top_plans,
        'total_month_plan_rev': total_month_plan_rev,
        'total_month_plan_count': total_month_plan_count,
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
    """User behavior analytics page with performance benchmarks and charts."""
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
    first_time_devices = max(0, unique_devices - returning_devices)
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
        'returning_devices': returning_devices,
        'first_time_devices': first_time_devices,
        'retention_rate': retention_rate,
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
            desc = sanitize_text(request.POST.get('description', ''), max_length=100)
            amount_raw = request.POST.get('amount', '').strip()
            if not desc or len(desc) < 2:
                messages.error(request, 'Cost description must be between 2 and 100 characters.')
            else:
                try:
                    amount = parse_bounded_int(amount_raw, 1, 10_000_000, 'Cost amount')
                    ProjectCost.objects.create(description=desc, amount=amount)
                    messages.success(request, f'Capital investment "{desc}" (₱{amount:,}) added.')
                except ValueError as e:
                    messages.error(request, str(e))
        elif action == 'delete_cost':
            cost_id = request.POST.get('cost_id')
            if cost_id:
                try:
                    ProjectCost.objects.filter(id=int(cost_id)).delete()
                    messages.success(request, 'Capital investment cost deleted.')
                except (ValueError, TypeError):
                    messages.error(request, 'Invalid cost ID.')
        elif action == 'add_expense':
            name = sanitize_text(request.POST.get('name', ''), max_length=100)
            amount_raw = request.POST.get('amount', '').strip()
            period = request.POST.get('period', 'monthly').strip().lower()
            if not name or len(name) < 2:
                messages.error(request, 'Expense name must be between 2 and 100 characters.')
            elif period not in ['daily', 'monthly', 'yearly']:
                messages.error(request, 'Invalid recurring period. Choose Daily, Monthly, or Yearly.')
            else:
                try:
                    amount = parse_bounded_int(amount_raw, 1, 10_000_000, 'Expense amount')
                    OperatingExpense.objects.create(name=name, amount=amount, period=period)
                    messages.success(request, f'Operating expense "{name}" (₱{amount:,}/{period}) added.')
                except ValueError as e:
                    messages.error(request, str(e))
        elif action == 'delete_expense':
            expense_id = request.POST.get('expense_id')
            if expense_id:
                try:
                    OperatingExpense.objects.filter(id=int(expense_id)).delete()
                    messages.success(request, 'Operating expense deleted.')
                except (ValueError, TypeError):
                    messages.error(request, 'Invalid expense ID.')
        return redirect('dashboard:roi')

    # === INVESTMENT (one-time capital project costs) ===
    total_investment = ProjectCost.total_cost()
    costs = ProjectCost.objects.all()

    # === GROSS REVENUE (all coins ever inserted) ===
    gross_revenue = CoinEvent.objects.aggregate(
        total=Sum('amount')
    )['total'] or 0

    # === OPERATING DAYS & DAY 1 (Capital Investment as Day 1) ===
    today = timezone.localdate()
    first_session = Session.objects.order_by('time_in').first()
    first_coin = CoinEvent.objects.order_by('timestamp').first()
    first_cost = ProjectCost.objects.order_by('date_added').first()

    first_dates = []
    if first_cost and first_cost.date_added:
        first_dates.append(first_cost.date_added)
    if first_session and first_session.time_in:
        first_dates.append(first_session.time_in)
    if first_coin and first_coin.timestamp:
        first_dates.append(first_coin.timestamp)

    if first_cost and first_cost.date_added:
        cost_date = timezone.localtime(first_cost.date_added).date()
        activity_dates = []
        if first_session and first_session.time_in:
            activity_dates.append(first_session.time_in)
        if first_coin and first_coin.timestamp:
            activity_dates.append(first_coin.timestamp)
        if activity_dates:
            activity_date = timezone.localtime(min(activity_dates)).date()
            day_1 = min(cost_date, activity_date)
        else:
            day_1 = cost_date
    elif first_dates:
        day_1 = timezone.localtime(min(first_dates)).date()
    else:
        day_1 = today

    day_1 = min(day_1, today)
    days_operating = max((today - day_1).days + 1, 1)

    operating_expenses = OperatingExpense.objects.all()
    total_expenses = OperatingExpense.calculate_total_expenses(days_operating)

    # === CUMULATIVE NET PROFIT BY DAY (HISTORICAL TRAJECTORY) ===
    from django.db.models.functions import TruncDate
    import zoneinfo
    import json
    tz_info = zoneinfo.ZoneInfo(settings.TIME_ZONE)

    # 1. Pre-fetch daily coins
    coin_daily_qs = CoinEvent.objects.filter(
        timestamp__date__gte=day_1,
        timestamp__date__lte=today
    ).annotate(
        day=TruncDate('timestamp', tzinfo=tz_info)
    ).values('day').annotate(
        total=Sum('amount')
    )
    coins_by_day = {c['day']: float(c['total'] or 0) for c in coin_daily_qs}
    has_coins = CoinEvent.objects.filter(timestamp__date__gte=day_1).exists()

    # 2. Pre-fetch daily sessions as fallback
    sess_daily_qs = Session.objects.filter(
        time_in__date__gte=day_1,
        time_in__date__lte=today,
        status__in=['active', 'expired', 'paused']
    ).annotate(
        day=TruncDate('time_in', tzinfo=tz_info)
    ).values('day').annotate(
        total=Sum('amount_paid')
    )
    sessions_by_day = {s['day']: float(s['total'] or 0) for s in sess_daily_qs}

    # 3. Calculate daily operating expense rate
    daily_expense_rate = 0.0
    for exp in operating_expenses:
        if exp.period == 'daily':
            daily_expense_rate += exp.amount
        elif exp.period == 'weekly':
            daily_expense_rate += exp.amount / 7.0
        elif exp.period == 'monthly':
            daily_expense_rate += exp.amount / 30.0
        elif exp.period == 'yearly':
            daily_expense_rate += exp.amount / 365.0

    profit_trend_labels = []
    profit_trend_values = []
    curr = day_1
    cum_rev = 0.0
    cum_exp = 0.0

    while curr <= today:
        if has_coins:
            d_rev = coins_by_day.get(curr, 0.0)
        else:
            d_rev = sessions_by_day.get(curr, 0.0)
        cum_rev += d_rev
        cum_exp += daily_expense_rate
        cum_profit = round(cum_rev - cum_exp, 2)

        profit_trend_labels.append(curr.strftime('%b %d'))
        profit_trend_values.append(cum_profit)
        curr += timedelta(days=1)

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
        'day_1_date': day_1,
        'profit_trend_labels': json.dumps(profit_trend_labels),
        'profit_trend_values': json.dumps(profit_trend_values),
        'active_page': 'roi',
    }
    return render(request, 'dashboard/roi.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def announcements_view(request):
    """Announcement management page."""
    # Clean up any stale or historical auto ISP outage notices so they never stack
    Announcement.objects.filter(message__contains="interrupted by our ISP", is_active=False).delete()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            message = sanitize_text(request.POST.get('message', ''), max_length=500, allow_multiline=True)
            if not message or len(message) < 3:
                messages.error(request, 'Announcement message must be at least 3 characters long.')
            else:
                Announcement.objects.create(message=message)
                messages.success(request, 'Announcement published successfully.')
        elif action == 'update':
            ann_id = request.POST.get('announcement_id')
            message = sanitize_text(request.POST.get('message', ''), max_length=500, allow_multiline=True)
            if not message or len(message) < 3:
                messages.error(request, 'Announcement message must be at least 3 characters long.')
            elif not ann_id:
                messages.error(request, 'Announcement ID is required.')
            else:
                try:
                    updated = Announcement.objects.filter(id=int(ann_id)).update(message=message)
                    if updated:
                        messages.success(request, 'Announcement updated successfully.')
                    else:
                        messages.error(request, 'Announcement not found.')
                except (ValueError, TypeError):
                    messages.error(request, 'Invalid announcement ID.')
        elif action == 'toggle':
            ann_id = request.POST.get('announcement_id')
            try:
                ann = Announcement.objects.get(id=int(ann_id))
                ann.is_active = not ann.is_active
                ann.save()
                status_text = 'activated' if ann.is_active else 'deactivated'
                messages.success(request, f'Announcement {status_text}.')
            except (Announcement.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Announcement not found.')
        elif action == 'delete':
            ann_id = request.POST.get('announcement_id')
            try:
                deleted, _ = Announcement.objects.filter(id=int(ann_id)).delete()
                if deleted:
                    messages.success(request, 'Announcement deleted successfully.')
                else:
                    messages.error(request, 'Announcement not found.')
            except (ValueError, TypeError):
                messages.error(request, 'Invalid announcement ID.')

        return redirect('dashboard:announcements')

    # Exclude system ISP outage announcements from admin announcement board
    announcements = Announcement.objects.exclude(message__contains="interrupted by our ISP")
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
                name = sanitize_text(request.POST.get('name', ''), max_length=50)
                price = parse_bounded_int(price_raw, 1, 50_000, "Price")
                duration_minutes = parse_bounded_int(duration_raw, 1, 43_200, "Duration")
                pause_limit = parse_bounded_int(pause_limit_raw, 0, 100, "Pause limit", default=0)
                pause_duration_limit = parse_bounded_int(pause_duration_limit_raw, 0, 1440, "Pause duration limit", default=0)

                if not name:
                    name = f"₱{price} Plan"

                speed_limit = None
                if speed_limit_raw:
                    sl_val = parse_bounded_float(speed_limit_raw, 0.1, 1000.0, "Download speed limit")
                    speed_limit = Decimal(str(sl_val))

                speed_limit_upload = None
                if speed_limit_upload_raw:
                    slu_val = parse_bounded_float(speed_limit_upload_raw, 0.1, 1000.0, "Upload speed limit")
                    speed_limit_upload = Decimal(str(slu_val))

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
                    messages.success(request, f'WiFi rate plan "{name}" created successfully.')
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
                        messages.success(request, f'WiFi rate plan "{name}" updated successfully.')
                    else:
                        error_message = 'Plan not found.'
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
    search = sanitize_text(request.GET.get('search', ''), max_length=60)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        incident_id_raw = request.POST.get('incident_id', '').strip()

        if action == 'manual_block':
            raw_mac = request.POST.get('mac_address', '').strip().upper()
            reason = sanitize_text(request.POST.get('reason', 'Manual Admin Block'), max_length=64) or 'Manual Admin Block'
            evidence = sanitize_text(request.POST.get('evidence', 'Manually blocked by administrator from Security page.'), max_length=500)
            
            import re
            if not re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', raw_mac):
                messages.error(request, f'Invalid MAC address format: "{raw_mac}". Expected format: AA:BB:CC:DD:EE:FF')
            else:
                mac_normalized = raw_mac.replace('-', ':').upper()
                iptables.block_device(mac_normalized)
                
                # Terminate any active or paused sessions for this MAC
                terminated_count = Session.objects.filter(
                    mac_address=mac_normalized,
                    status__in=['active', 'paused']
                ).update(status='expired', time_out=timezone.now())

                incident, created = SuspiciousDevice.objects.get_or_create(
                    mac_address=mac_normalized,
                    defaults={
                        'reason': reason,
                        'evidence': evidence,
                        'status': SuspiciousDevice.STATUS_BLOCKED,
                        'is_blocked': True,
                        'blocked_at': timezone.now(),
                        'resolved_by': request.user.username,
                    }
                )
                if not created:
                    incident.mark_blocked(by=request.user.username)
                    incident.reason = reason
                    if evidence:
                        incident.evidence = evidence
                    incident.save()

                term_msg = f" ({terminated_count} active session(s) terminated)" if terminated_count else ""
                messages.success(request, f'Device {mac_normalized} has been manually blocked.{term_msg}')
                audit_logger.info(
                    'event=suspicious_device_manual_block user=%s mac=%s ip=%s terminated_sessions=%s',
                    request.user.username,
                    mac_normalized,
                    _client_ip(request),
                    terminated_count,
                )
            return _safe_redirect_referer(request, fallback='dashboard:security')

        # Existing incident actions
        try:
            inc_id = parse_bounded_int(incident_id_raw, 1, 2147483647, 'incident_id')
            incident = SuspiciousDevice.objects.filter(id=inc_id).first()
        except ValueError:
            incident = None

        if not incident:
            messages.error(request, 'Suspicious device record not found.')
        elif action == 'block':
            blocked = iptables.block_device(incident.mac_address)
            incident.mark_blocked(by=request.user.username)
            # Terminate any active or paused sessions in database immediately
            terminated_count = Session.objects.filter(
                mac_address=incident.mac_address,
                status__in=['active', 'paused']
            ).update(status='expired', time_out=timezone.now())

            term_msg = f" ({terminated_count} active session(s) terminated)" if terminated_count else ""
            messages.success(request, f'Device {incident.mac_address} blocked successfully.{term_msg}')
            audit_logger.info(
                'event=suspicious_device_blocked user=%s mac=%s ip=%s terminated_sessions=%s',
                request.user.username,
                incident.mac_address,
                _client_ip(request),
                terminated_count,
            )
        elif action == 'unblock':
            incident.mark_cleared(by=request.user.username)
            # Only allow through firewall if device currently has an active unexpired session
            active_session = Session.objects.filter(
                mac_address=incident.mac_address,
                status='active'
            ).select_related('plan').first()
            if active_session:
                rate = active_session.plan.speed_limit if active_session.plan else None
                rate_kbps = int(rate * 1024) if rate else None
                iptables.allow_device(incident.mac_address, rate_kbps=rate_kbps)
                session_note = " Active session restored."
            else:
                # Ensure device remains in captive portal redirect state (prevent free internet exploit)
                iptables.block_device(incident.mac_address)
                session_note = " Ready for captive portal login (no active session)."

            messages.success(request, f'Device {incident.mac_address} unblocked.{session_note}')
            audit_logger.info(
                'event=suspicious_device_unblocked user=%s mac=%s ip=%s has_active_session=%s',
                request.user.username,
                incident.mac_address,
                _client_ip(request),
                bool(active_session),
            )
        elif action == 'false_positive':
            incident.mark_false_positive(by=request.user.username)
            messages.success(request, f'Device {incident.mac_address} marked as false positive.')
            audit_logger.info(
                'event=suspicious_device_false_positive user=%s mac=%s ip=%s',
                request.user.username,
                incident.mac_address,
                _client_ip(request),
            )
        elif action == 'clear':
            incident.mark_cleared(by=request.user.username)
            messages.success(request, f'Device {incident.mac_address} marked as cleared.')
            audit_logger.info(
                'event=suspicious_device_cleared user=%s mac=%s ip=%s',
                request.user.username,
                incident.mac_address,
                _client_ip(request),
            )
        elif action == 'delete':
            mac = incident.mac_address
            incident.delete()
            messages.success(request, f'Alert record for {mac} deleted.')
            audit_logger.info(
                'event=suspicious_device_deleted user=%s mac=%s ip=%s',
                request.user.username,
                mac,
                _client_ip(request),
            )
        else:
            messages.error(request, 'Unsupported action.')

        return _safe_redirect_referer(request, fallback='dashboard:security')

    suspicious_devices = SuspiciousDevice.objects.all()

    valid_statuses = dict(SuspiciousDevice.STATUS_CHOICES).keys()
    if status_filter and status_filter in valid_statuses:
        suspicious_devices = suspicious_devices.filter(status=status_filter)
    else:
        status_filter = ''

    if search:
        clean_search = search.strip()
        suspicious_devices = suspicious_devices.filter(
            Q(mac_address__icontains=clean_search)
            | Q(last_ip_address__icontains=clean_search)
            | Q(reason__icontains=clean_search)
            | Q(evidence__icontains=clean_search)
        )

    counts = {
        'new': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_NEW).count(),
        'blocked': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_BLOCKED).count(),
        'false_positive': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_FALSE_POSITIVE).count(),
        'cleared': SuspiciousDevice.objects.filter(status=SuspiciousDevice.STATUS_CLEARED).count(),
    }

    # Pagination: 20 devices per page
    page = request.GET.get('page', 1)
    paginator = Paginator(suspicious_devices, 20)
    try:
        devices_page = paginator.page(page)
    except PageNotAnInteger:
        devices_page = paginator.page(1)
    except EmptyPage:
        devices_page = paginator.page(paginator.num_pages)

    context = {
        'active_page': 'security',
        'suspicious_devices': devices_page,
        'status_filter': status_filter,
        'search': search,
        'status_choices': SuspiciousDevice.STATUS_CHOICES,
        'counts': counts,
    }
    return render(request, 'dashboard/security.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def account_view(request):
    """Dashboard account settings for admin email, password, and multi-admin management."""
    if not request.user.is_authenticated:
        return redirect('dashboard:login')

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
            if not email:
                email_error = 'Email cannot be empty.'
            else:
                try:
                    validate_email(email)
                    request.user.email = email
                    request.user.save(update_fields=['email'])
                    email_message = 'Email updated successfully.'
                    audit_logger.info("event=admin_email_updated user=%s email=%s", request.user.username, email)
                except ValidationError:
                    email_error = 'Please enter a valid email address (e.g., admin@example.com).'

        elif action == 'change_password':
            password_form = PasswordChangeForm(request.user, request.POST)
            new_pass = request.POST.get('new_password1', '')
            pass_ok, pass_err = validate_password_strength(new_pass, request.user.username)
            if not pass_ok:
                password_form.add_error('new_password1', pass_err)
            elif password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                password_message = 'Password updated successfully.'
                audit_logger.info("event=admin_password_updated user=%s", request.user.username)
                password_form = PasswordChangeForm(request.user)

        elif action == 'create_admin':
            new_username = request.POST.get('new_username', '').strip()
            new_email = request.POST.get('new_email', '').strip()
            new_password = request.POST.get('new_password', '').strip()

            u_ok, u_err = validate_username(new_username)
            p_ok, p_err = validate_password_strength(new_password, new_username)

            if not u_ok:
                admin_error = u_err
            elif User.objects.filter(username__iexact=new_username).exists():
                admin_error = f'Username "{new_username}" is already taken.'
            elif new_email:
                try:
                    validate_email(new_email)
                except ValidationError:
                    admin_error = 'Please enter a valid email address.'

            if not admin_error and not p_ok:
                admin_error = p_err

            if not admin_error:
                try:
                    # All accounts created are Superadmins (Superadmin role only)
                    new_user = User.objects.create_user(
                        username=new_username,
                        email=new_email,
                        password=new_password,
                    )
                    new_user.is_staff = True
                    new_user.is_superuser = True
                    new_user.is_active = True
                    new_user.save()

                    admin_message = f'Superadmin account "{new_username}" created successfully.'
                    audit_logger.info(
                        "event=admin_created creator=%s created_user=%s is_superuser=True",
                        request.user.username, new_username
                    )
                except Exception as e:
                    admin_error = f'Error creating account: {e}'

        elif action == 'delete_admin':
            target_id = request.POST.get('target_user_id')
            try:
                target_user = User.objects.get(id=target_id, is_staff=True)
                if target_user.id == request.user.id:
                    admin_error = 'You cannot delete your own active administrator account.'
                elif User.objects.filter(is_staff=True).count() <= 1:
                    admin_error = 'Cannot delete the only remaining administrator account.'
                else:
                    deleted_name = target_user.username
                    target_user.delete()
                    admin_message = f'Administrator account "{deleted_name}" deleted successfully.'
                    audit_logger.info(
                        "event=admin_deleted by=%s deleted_user=%s",
                        request.user.username, deleted_name
                    )
            except (User.DoesNotExist, ValueError):
                admin_error = 'User not found.'
            except Exception as e:
                admin_error = f'Error deleting account: {e}'

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

    if request.method == 'POST':
        try:
            # Networking
            settings_obj.enable_anti_tethering = request.POST.get('enable_anti_tethering') == 'on'
            settings_obj.enable_sqm = request.POST.get('enable_sqm') == 'on'
            settings_obj.isp_download_speed = parse_bounded_int(
                request.POST.get('isp_download_speed'), 1, 10_000, "ISP Download Speed", default=settings_obj.isp_download_speed
            )
            settings_obj.isp_upload_speed = parse_bounded_int(
                request.POST.get('isp_upload_speed'), 1, 10_000, "ISP Upload Speed", default=settings_obj.isp_upload_speed
            )
            
            # General / UI
            settings_obj.enable_dark_mode = request.POST.get('enable_dark_mode') == 'on'
            settings_obj.max_concurrent_sessions = parse_bounded_int(
                request.POST.get('max_concurrent_sessions'), 1, 1_000, "Max Concurrent Sessions", default=settings_obj.max_concurrent_sessions
            )
            settings_obj.global_pause_limit_hours = parse_bounded_int(
                request.POST.get('global_pause_limit_hours'), 0, 720, "Global Max Pause Hours", default=settings_obj.global_pause_limit_hours
            )
            
            # Network & Automation Features
            settings_obj.enable_internet_check = request.POST.get('enable_internet_check') == 'on'
            settings_obj.enable_outage_announcement = request.POST.get('enable_outage_announcement') == 'on'
            settings_obj.enable_outage_auto_pause = request.POST.get('enable_outage_auto_pause') == 'on'
            settings_obj.enable_auto_pause_resume = request.POST.get('enable_auto_pause_resume') == 'on'
            settings_obj.auto_pause_timeout_seconds = parse_bounded_int(
                request.POST.get('auto_pause_timeout_seconds'), 60, 86_400, "Auto-Pause Timeout", default=settings_obj.auto_pause_timeout_seconds
            )
            settings_obj.insert_coin_countdown_seconds = parse_bounded_int(
                request.POST.get('insert_coin_countdown_seconds'), 10, 600, "Insert Coin Countdown", default=settings_obj.insert_coin_countdown_seconds
            )
            if 'coin_timer_extension_seconds' in request.POST:
                settings_obj.coin_timer_extension_seconds = parse_bounded_int(
                    request.POST.get('coin_timer_extension_seconds'), 1, 60, "Coin Timer Extension", default=settings_obj.coin_timer_extension_seconds
                )
            if 'coin_timer_min_remaining_seconds' in request.POST:
                settings_obj.coin_timer_min_remaining_seconds = parse_bounded_int(
                    request.POST.get('coin_timer_min_remaining_seconds'), 5, 60, "Coin Timer Minimum", default=settings_obj.coin_timer_min_remaining_seconds
                )
            if 'coin_timer_max_seconds' in request.POST:
                settings_obj.coin_timer_max_seconds = parse_bounded_int(
                    request.POST.get('coin_timer_max_seconds'), 30, 600, "Coin Timer Maximum", default=settings_obj.coin_timer_max_seconds
                )

            # Relational validation for coin timer countdown
            if settings_obj.coin_timer_min_remaining_seconds > settings_obj.coin_timer_max_seconds:
                raise ValueError("Coin Timer Minimum floor cannot exceed Coin Timer Maximum ceiling.")
            if settings_obj.coin_timer_max_seconds < settings_obj.insert_coin_countdown_seconds:
                raise ValueError(
                    f"Coin Timer Maximum ceiling ({settings_obj.coin_timer_max_seconds}s) cannot be less than "
                    f"the Initial Coin Timer ({settings_obj.insert_coin_countdown_seconds}s)."
                )
            
            # Gamification
            settings_obj.enable_spin_wheel = request.POST.get('enable_spin_wheel') == 'on'
            settings_obj.spin_cost_points = parse_bounded_int(
                request.POST.get('spin_cost_points'), 1, 10_000, "Spin Cost Points", default=settings_obj.spin_cost_points
            )
            settings_obj.daily_spin_limit = parse_bounded_int(
                request.POST.get('daily_spin_limit'), 1, 100, "Daily Spin Limit", default=settings_obj.daily_spin_limit
            )
            settings_obj.points_per_streak_day = parse_bounded_int(
                request.POST.get('points_per_streak_day'), 0, 1_000, "Points Per Streak Day", default=settings_obj.points_per_streak_day
            )
            if 'points_per_peso' in request.POST:
                settings_obj.points_per_peso = parse_bounded_int(
                    request.POST.get('points_per_peso'), 0, 1_000, "Points Per Peso", default=settings_obj.points_per_peso
                )
            
            # Family Pass
            if 'enable_family_pass' in request.POST or 'family_pass_base_rate' in request.POST:
                settings_obj.enable_family_pass = request.POST.get('enable_family_pass') == 'on'
                if request.POST.get('family_pass_base_rate'):
                    settings_obj.family_pass_base_rate = parse_bounded_int(
                        request.POST.get('family_pass_base_rate'), 1, 10_000, "Family Pass Base Rate", default=settings_obj.family_pass_base_rate
                    )
                if request.POST.get('family_pass_device_rate'):
                    settings_obj.family_pass_device_rate = parse_bounded_int(
                        request.POST.get('family_pass_device_rate'), 1, 10_000, "Family Pass Extra Device Rate", default=settings_obj.family_pass_device_rate
                    )
                if request.POST.get('family_pass_max_devices'):
                    settings_obj.family_pass_max_devices = parse_bounded_int(
                        request.POST.get('family_pass_max_devices'), 2, 50, "Family Pass Max Devices", default=settings_obj.family_pass_max_devices
                    )
                if request.POST.get('family_pass_speed_limit'):
                    settings_obj.family_pass_speed_limit = parse_bounded_float(
                        request.POST.get('family_pass_speed_limit'), 0.1, 1000.0, "Family Pass Speed Limit", default=settings_obj.family_pass_speed_limit
                    )
                if request.POST.get('family_pass_speed_limit_upload'):
                    settings_obj.family_pass_speed_limit_upload = parse_bounded_float(
                        request.POST.get('family_pass_speed_limit_upload'), 0.1, 1000.0, "Family Pass Upload Limit", default=settings_obj.family_pass_speed_limit_upload
                    )
            if 'group_code_expiry_hours' in request.POST:
                settings_obj.group_code_expiry_hours = parse_bounded_int(
                    request.POST.get('group_code_expiry_hours'), 0, 720, "Group Code Expiry Hours", default=settings_obj.group_code_expiry_hours
                )
            
            # Telegram Bot Integration
            if 'telegram_bot_token' in request.POST or 'enable_telegram_bot' in request.POST:
                settings_obj.enable_telegram_bot = request.POST.get('enable_telegram_bot') == 'on'
                if 'telegram_bot_token' in request.POST:
                    token = request.POST.get('telegram_bot_token', '').strip()
                    if token and not re.match(r'^\d{6,15}:[A-Za-z0-9_-]{25,60}$', token):
                        raise ValueError("Telegram Bot Token format appears invalid. It should look like 123456789:ABCdef-gh1234_xyz.")
                    settings_obj.telegram_bot_token = token
                if 'telegram_admin_chat_id' in request.POST:
                    chat_id = request.POST.get('telegram_admin_chat_id', '').strip()
                    if chat_id and not re.match(r'^-?\d{5,25}$', chat_id):
                        raise ValueError("Telegram Admin Chat ID must be numeric (e.g. 6261306648).")
                    settings_obj.telegram_admin_chat_id = chat_id
                settings_obj.telegram_notify_tickets = request.POST.get('telegram_notify_tickets') == 'on'
                settings_obj.telegram_notify_isp_down = request.POST.get('telegram_notify_isp_down') == 'on'
                settings_obj.telegram_notify_daily_summary = request.POST.get('telegram_notify_daily_summary') == 'on'

            settings_obj.save()
            audit_logger.info("event=settings_updated user=%s ip=%s", request.user.username, _client_ip(request))
            message = "Settings updated successfully."

            # Test telegram ping if requested
            if request.POST.get('test_telegram') == '1':
                from dashboard.telegram_bot import send_telegram_message
                test_sent = send_telegram_message(
                    "🔔 *Test Notification from iConnect Admin Console!*\nYour settings are saved and Telegram alerts are connected! 🚀"
                )
                if test_sent:
                    message += " (Test message sent to Telegram successfully!)"
                else:
                    message += " (Note: Could not send test Telegram ping, please verify token and Chat ID)."

            # Apply network settings immediately if on Linux
            try:
                from sessions_app.iptables import apply_network_settings
                apply_network_settings()
            except Exception as e:
                logging.error(f"Failed to apply network settings: {e}")
                message += " (Note: Network rules could not be applied, please check logs)."

            messages.success(request, message)
            return _safe_redirect_referer(request, fallback='dashboard:settings')

        except ValueError as e:
            messages.error(request, str(e))
            return _safe_redirect_referer(request, fallback='dashboard:settings')
        except Exception as e:
            messages.error(request, f"An error occurred: {e}")
            return _safe_redirect_referer(request, fallback='dashboard:settings')

    context = {
        'active_page': 'settings',
        'settings': settings_obj,
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

    elif action == 'add_time':
        try:
            data = json.loads(request.body) if request.body else {}
            minutes = parse_bounded_int(data.get('minutes'), 1, 10080, "Additional minutes")
            plan_id = data.get('plan_id')
            if plan_id not in (None, ''):
                plan_id = parse_bounded_int(plan_id, 1, 1000000, "Plan ID")
            else:
                plan_id = None

            speed_limit = None
            if data.get('speed_limit') not in (None, ''):
                speed_limit = parse_bounded_float(data.get('speed_limit'), 0.1, 1000.0, "Download speed limit")

            speed_limit_upload = None
            if data.get('speed_limit_upload') not in (None, ''):
                speed_limit_upload = parse_bounded_float(data.get('speed_limit_upload'), 0.1, 1000.0, "Upload speed limit")
        except ValueError as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        except Exception:
            return JsonResponse({'success': False, 'error': 'Invalid request data'}, status=400)

        if plan_id:
            try:
                selected_plan = Plan.objects.filter(id=plan_id, is_active=True).first()
                if selected_plan:
                    session.plan = selected_plan
            except Exception as e:
                logging.warning(f"Failed to assign plan {plan_id} on add_time: {e}")

        was_expired = (session.status == 'expired')
        session.extend_session(minutes)
        if was_expired:
            session.status = 'active'
            session.time_out = None
            session.total_paused_seconds = 0
            session.paused_at = None
        session.save()

        # Re-allow in firewall / update bandwidth shaping if active
        if session.status == 'active':
            try:
                from sessions_app.iptables import allow_device
                if speed_limit is not None:
                    dl_kbps = int(speed_limit * 1024)
                elif session.plan and session.plan.speed_limit:
                    dl_kbps = int(session.plan.speed_limit * 1024)
                else:
                    dl_kbps = None

                if speed_limit_upload is not None:
                    ul_kbps = int(speed_limit_upload * 1024)
                elif session.plan and session.plan.speed_limit_upload:
                    ul_kbps = int(session.plan.speed_limit_upload * 1024)
                else:
                    ul_kbps = dl_kbps

                allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
            except Exception as e:
                logging.error(f"Failed to allow device on add_time: {e}")

        audit_logger.info(
            'event=admin_add_time admin=%s mac=%s added_minutes=%d session_id=%s plan_id=%s dl_mbps=%s ul_mbps=%s ip=%s',
            request.user.username,
            session.mac_address,
            minutes,
            session.id,
            session.plan_id,
            speed_limit,
            speed_limit_upload,
            _client_ip(request),
        )
        return JsonResponse({
            'success': True,
            'message': f'Added {minutes} minutes to session #{session.id} ({session.mac_address})',
            'duration_minutes_purchased': session.duration_minutes_purchased,
            'status': session.status,
            'plan_name': session.plan.name if session.plan else 'Custom',
        })

    elif action == 'edit':
        try:
            data = json.loads(request.body)
            new_name = sanitize_text(data.get('device_name', ''), max_length=60)
            if not new_name:
                return JsonResponse({'success': False, 'error': 'Device name cannot be empty (max 60 characters)'}, status=400)
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

    search_mac = sanitize_text(request.GET.get('mac', ''), max_length=30).upper()

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
                settings_obj.spin_cost_points = parse_bounded_int(request.POST.get("spin_cost_points"), 1, 10_000, "Points to spin", default=10)
                settings_obj.points_per_streak_day = parse_bounded_int(request.POST.get("points_per_streak_day"), 0, 1_000, "Points per streak day", default=5)
                settings_obj.points_per_peso = parse_bounded_int(request.POST.get("points_per_peso"), 0, 1_000, "Points per peso", default=1)
                settings_obj.save()
                messages.success(request, "Gamification point rules updated successfully.")
            except ValueError as e:
                messages.error(request, str(e))
                
        elif action == "add_prize" or action == "edit_prize":
            try:
                prize_id = request.POST.get("prize_id")
                name = sanitize_text(request.POST.get("name", ""), max_length=50)
                if not name or len(name) < 2:
                    raise ValueError("Prize name must be between 2 and 50 characters.")

                minutes = parse_bounded_int(request.POST.get("minutes_reward"), 0, 43_200, "Minutes reward", default=0)
                weight = parse_bounded_int(request.POST.get("probability_weight"), 1, 1_000, "Probability weight", default=10)
                is_active = request.POST.get("is_active") == "on"
                
                speed_limit = request.POST.get("speed_limit")
                speed_limit = parse_bounded_float(speed_limit, 0.1, 1000.0, "Speed limit") if speed_limit else None
                
                speed_limit_upload = request.POST.get("speed_limit_upload")
                speed_limit_upload = parse_bounded_float(speed_limit_upload, 0.1, 1000.0, "Upload speed limit") if speed_limit_upload else None
                
                pause_limit = parse_bounded_int(request.POST.get("pause_limit"), 0, 100, "Pause limit", default=0)
                pause_duration_limit = parse_bounded_int(request.POST.get("pause_duration_limit"), 0, 1440, "Pause duration limit", default=0)
                
                if action == "edit_prize" and prize_id:
                    prize = SpinPrize.objects.get(id=int(prize_id))
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
            except ValueError as e:
                messages.error(request, str(e))
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
            # Use SQLite online backup API to flush WAL frames and guarantee atomic snapshot
            src_conn = sqlite3.connect(str(found_path))
            dest_conn = sqlite3.connect(':memory:')
            with dest_conn:
                src_conn.backup(dest_conn)
            src_conn.close()

            if hasattr(dest_conn, 'serialize'):
                content = dest_conn.serialize()
            else:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False) as tmp:
                    tmp_name = tmp.name
                file_dest = sqlite3.connect(tmp_name)
                with file_dest:
                    dest_conn.backup(file_dest)
                file_dest.close()
                with open(tmp_name, 'rb') as f:
                    content = f.read()
                Path(tmp_name).unlink(missing_ok=True)
            dest_conn.close()

            response = HttpResponse(content, content_type='application/x-sqlite3')
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            response['Content-Disposition'] = f'attachment; filename="iconnect_backup_{timestamp}.sqlite3"'
            audit_logger.info("event=database_backup_download user=%s size=%d", request.user.username, len(content))
            return response
        except Exception as e:
            logger.error(f"Error executing SQLite online backup: {e}")

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
    search_query = sanitize_text(request.GET.get('q', ''), max_length=100)

    reports = IssueReport.objects.all()

    if status_filter in ['pending', 'resolved']:
        reports = reports.filter(status=status_filter)

    if category_filter in dict(IssueReport.CATEGORY_CHOICES).keys():
        reports = reports.filter(category=category_filter)

    if search_query:
        clean_id = search_query.lstrip('#').strip()
        q_filter = (
            Q(mac_address__icontains=search_query) |
            Q(contact_info__icontains=search_query) |
            Q(message__icontains=search_query)
        )
        if clean_id.isdigit():
            q_filter = Q(id=int(clean_id)) | q_filter
        reports = reports.filter(q_filter)

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
        issue.admin_notes = sanitize_text(admin_notes, max_length=1000, allow_multiline=True)

    issue.save()
    messages.success(request, f'Ticket #{issue.id} updated.')
    return _safe_redirect_referer(request, fallback='dashboard:issues')


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
@require_POST
def delete_issue(request, issue_id):
    """Delete an issue ticket."""
    issue = get_object_or_404(IssueReport, id=issue_id)
    issue_num = issue.id
    issue.delete()
    messages.success(request, f'Ticket #{issue_num} deleted.')
    return _safe_redirect_referer(request, fallback='dashboard:issues')


