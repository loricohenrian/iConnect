"""
Dashboard Views — API endpoints and template views for admin dashboard
"""
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
from django.http import JsonResponse
from django.core.cache import cache
from django.contrib.auth import update_session_auth_hash, authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Announcement, RevenueGoal, ProjectCost, DailyRevenueSummary
from .serializers import AnnouncementSerializer, RevenueGoalSerializer, ProjectCostSerializer
from sessions_app import iptables
from sessions_app.models import Session, CoinEvent, Plan, WhitelistedDevice, SuspiciousDevice
from django.conf import settings


logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('audit')


def _is_dashboard_admin(user):
    return user.is_authenticated and user.is_staff


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


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

    today = timezone.now().date()
    week_ago = today - timedelta(days=7)

    # Revenue today
    revenue_today = Session.objects.filter(
        time_in__date=today, status__in=['active', 'expired']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0

    # Connected users
    connected_count = Session.objects.filter(status='active').count()
    whitelisted_count = WhitelistedDevice.objects.count()

    # Total bandwidth today
    bandwidth_today = Session.objects.filter(
        time_in__date=today
    ).aggregate(total=Sum('bandwidth_used_mb'))['total'] or 0

    # ROI
    total_cost = ProjectCost.total_cost()
    total_revenue = Session.objects.filter(
        status__in=['active', 'expired']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0
    roi_percentage = (total_revenue / total_cost * 100) if total_cost > 0 else 0

    # Revenue last 7 days
    daily_revenue = Session.objects.filter(
        time_in__date__gte=week_ago,
        status__in=['active', 'expired']
    ).annotate(
        day=TruncDate('time_in')
    ).values('day').annotate(
        revenue=Sum('amount_paid'),
        sessions=Count('id')
    ).order_by('day')

    # Sessions today
    sessions_today = Session.objects.filter(time_in__date=today).count()

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
    })


@api_view(['GET'])
def system_stats_api(request):
    """System hardware stats (CPU temp, load, RAM, disk)."""
    import shutil
    import os

    stats = {
        'cpu_temp': 'N/A',
        'cpu_load': 'N/A',
        'ram_used': 'N/A',
        'ram_total': 'N/A',
        'ram_percent': 0,
        'disk_used': 'N/A',
        'disk_total': 'N/A',
        'disk_percent': 0,
    }

    # CPU Temperature
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_raw = int(f.read().strip())
            stats['cpu_temp'] = f"{temp_raw / 1000:.1f}°C"
    except Exception:
        pass

    # CPU Load (1-min average)
    try:
        with open('/proc/loadavg', 'r') as f:
            load = f.read().split()[0]
            stats['cpu_load'] = f"{float(load):.1f}%"
    except Exception:
        try:
            stats['cpu_load'] = f"{os.getloadavg()[0]:.1f}%"
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
            stats['ram_total'] = f"{total_mb:.0f} MB"
            stats['ram_used'] = f"{used_mb:.0f} MB"
            stats['ram_percent'] = round((used_mb / total_mb) * 100) if total_mb > 0 else 0
    except Exception:
        pass

    # Disk
    try:
        usage = shutil.disk_usage('/')
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        stats['disk_total'] = f"{total_gb:.1f} GB"
        stats['disk_used'] = f"{used_gb:.1f} GB"
        stats['disk_percent'] = round((usage.used / usage.total) * 100) if usage.total > 0 else 0
    except Exception:
        pass

    return Response(stats)


@api_view(['GET'])
def heatmap_data_api(request):
    """
    GET /api/dashboard/heatmap/ — Returns peak hours heatmap data
    """
    if not _is_dashboard_admin(request.user):
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

    week_ago = timezone.now().date() - timedelta(days=7)

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
    today = timezone.now().date()

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


# ============================================
# TEMPLATE VIEWS (Admin Dashboard Pages)
# ============================================

@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def overview(request):
    """Admin dashboard overview page."""
    today = timezone.now().date()

    revenue_today = Session.objects.filter(
        time_in__date=today, status__in=['active', 'expired']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0

    connected = Session.objects.filter(status='active').count()
    whitelisted = WhitelistedDevice.objects.count()
    sessions_today = Session.objects.filter(time_in__date=today).count()

    total_cost = ProjectCost.total_cost()
    total_revenue = Session.objects.filter(
        status__in=['active', 'expired']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0
    roi_pct = (total_revenue / total_cost * 100) if total_cost > 0 else 0

    recent_sessions = Session.objects.select_related('plan').all()[:10]
    announcements = Announcement.objects.filter(is_active=True)

    # Solar savings
    system_watts = getattr(settings, 'PISONET_SYSTEM_WATTAGE', 18)
    elec_rate = getattr(settings, 'PISONET_ELECTRICITY_RATE', 11.0)
    monthly_savings = (system_watts / 1000) * 24 * 30 * elec_rate

    context = {
        'revenue_today': revenue_today,
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
    goal_message = ''
    goal_error = ''

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_goal':
            period = request.POST.get('period', '').strip()
            target_amount_raw = request.POST.get('target_amount', '').strip()

            if period not in ('daily', 'weekly'):
                goal_error = 'Invalid goal period.'
            else:
                try:
                    target_amount = int(target_amount_raw)
                    if target_amount <= 0:
                        raise ValueError('Target amount must be greater than zero.')

                    RevenueGoal.objects.update_or_create(
                        period=period,
                        defaults={'target_amount': target_amount}
                    )
                    goal_message = f'{period.title()} revenue goal saved.'
                except ValueError as exc:
                    goal_error = str(exc)

    goals = {
        item['period']: item['target_amount']
        for item in RevenueGoal.objects.values('period', 'target_amount')
    }

    context = {
        'active_page': 'revenue',
        'goals': goals,
        'goal_message': goal_message,
        'goal_error': goal_error,
    }
    return render(request, 'dashboard/revenue.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def sessions_view(request):
    """Session logs page."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')

    sessions = Session.objects.select_related('plan').all()

    if status_filter:
        sessions = sessions.filter(status=status_filter)
    if search:
        sessions = sessions.filter(
            Q(mac_address__icontains=search) |
            Q(device_name__icontains=search) |
            Q(ip_address__icontains=search)
        )

    context = {
        'sessions': sessions[:100],
        'status_filter': status_filter,
        'search': search,
        'active_page': 'sessions',
    }
    return render(request, 'dashboard/sessions.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def reports(request):
    """Reports page."""
    context = {
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
    """User behavior analytics page."""
    today = timezone.now().date()
    month_ago = today - timedelta(days=30)

    # Plan popularity
    plan_stats = Session.objects.filter(
        time_in__date__gte=month_ago,
        status__in=['active', 'expired']
    ).values('plan__name').annotate(
        count=Count('id'),
        total_revenue=Sum('amount_paid'),
        avg_duration=Avg('duration_minutes_purchased')
    ).order_by('-count')

    # Average session duration
    avg_duration = Session.objects.filter(
        time_in__date__gte=month_ago,
        status__in=['active', 'expired']
    ).aggregate(avg=Avg('duration_minutes_purchased'))['avg'] or 0

    # Total bandwidth
    total_bandwidth = Session.objects.filter(
        time_in__date__gte=month_ago
    ).aggregate(total=Sum('bandwidth_used_mb'))['total'] or 0

    context = {
        'plan_stats': plan_stats,
        'avg_duration': round(avg_duration, 1),
        'total_bandwidth': round(total_bandwidth, 1),
        'active_page': 'analytics',
    }
    return render(request, 'dashboard/analytics.html', context)


@user_passes_test(_is_dashboard_admin, login_url='dashboard:login')
def roi(request):
    """ROI tracker page."""
    total_cost = ProjectCost.total_cost()
    total_revenue = Session.objects.filter(
        status__in=['active', 'expired']
    ).aggregate(total=Sum('amount_paid'))['total'] or 0

    roi_pct = (total_revenue / total_cost * 100) if total_cost > 0 else 0

    # Calculate projected breakeven
    first_session = Session.objects.order_by('time_in').first()
    if first_session and total_revenue > 0:
        days_operating = (timezone.now() - first_session.time_in).days or 1
        daily_avg = total_revenue / days_operating
        remaining = max(0, total_cost - total_revenue)
        days_to_breakeven = int(remaining / daily_avg) if daily_avg > 0 else 0
        projected_date = timezone.now().date() + timedelta(days=days_to_breakeven)
    else:
        daily_avg = 0
        days_to_breakeven = 0
        projected_date = None

    costs = ProjectCost.objects.all()

    context = {
        'total_cost': total_cost,
        'total_revenue': total_revenue,
        'roi_percentage': round(roi_pct, 1),
        'daily_avg_revenue': round(daily_avg, 0),
        'days_to_breakeven': days_to_breakeven,
        'projected_breakeven': projected_date,
        'costs': costs,
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
            is_active = request.POST.get('is_active') in ('on', 'true', '1')

            try:
                price = int(price_raw)
                duration_minutes = int(duration_raw)
                if not name:
                    raise ValueError('Plan name is required.')
                if price <= 0 or duration_minutes <= 0:
                    raise ValueError('Price and duration must be positive.')

                speed_limit = None
                if speed_limit_raw:
                    speed_limit = Decimal(speed_limit_raw)
                    if speed_limit <= 0:
                        raise ValueError('Speed limit must be positive when provided.')

                if action == 'create':
                    Plan.objects.create(
                        name=name,
                        price=price,
                        duration_minutes=duration_minutes,
                        speed_limit=speed_limit,
                        is_active=is_active,
                    )
                else:
                    plan = Plan.objects.filter(id=plan_id).first()
                    if plan:
                        plan.name = name
                        plan.price = price
                        plan.duration_minutes = duration_minutes
                        plan.speed_limit = speed_limit
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
                try:
                    plan.delete()
                except ProtectedError:
                    error_message = 'Cannot delete this plan because it is already used by existing sessions. Set it inactive instead.'

        if not error_message:
            return redirect('dashboard:plans')

    context = {
        'plans': Plan.objects.all().order_by('price', 'id'),
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
    """Dashboard account settings for admin email and password."""
    if not request.user.is_authenticated:
        return redirect(f'/admin/login/?next={request.path}')

    email_message = ''
    email_error = ''
    password_message = ''
    password_form = PasswordChangeForm(request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_email':
            email = request.POST.get('email', '').strip()
            if email:
                request.user.email = email
                request.user.save(update_fields=['email'])
                email_message = 'Email updated successfully.'
            else:
                email_error = 'Email cannot be empty.'
        elif action == 'change_password':
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                password_message = 'Password updated successfully.'
                password_form = PasswordChangeForm(request.user)

    context = {
        'active_page': 'account',
        'email_message': email_message,
        'email_error': email_error,
        'password_message': password_message,
        'password_form': password_form,
    }
    return render(request, 'dashboard/account.html', context)



