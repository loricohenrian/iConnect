"""Comparable revenue periods; a zero baseline is not 100% growth."""
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from sessions_app.models import CoinEvent, Session


def analytics_dates(params, today):
    aliases = {'daily': 'today', 'weekly': 'week', 'monthly': 'month',
               'yearly': 'year', 'all_time': 'all'}
    period = params.get('period', 'month')
    period = aliases.get(period, period)
    if period == 'all':
        return period, None, None
    if period == 'custom':
        try:
            start = parse_date(params.get('start_date') or params.get('custom_start') or '')
            end = parse_date(params.get('end_date') or params.get('custom_end') or '')
        except ValueError:
            start = end = None
        if start and end:
            start, end = sorted((start, end))
            end = min(end, today)
            if start <= end:
                return period, start, end
        # Invalid/unbounded/future-only ranges must not produce a fabricated
        # percentage or silently compare an all-time sum with a 30-day period.
        period = 'month'
    if period == 'today':
        start = today
    elif period == 'week':
        start = today - timedelta(days=today.weekday())
    elif period == 'year':
        start = today.replace(month=1, day=1)
    else:
        period = 'month'
        start = today - timedelta(days=29)  # inclusive: exactly 30 calendar dates
    return period, start, today


def revenue_benchmark(period, start, end, now):
    """Use the Revenue page's coin ledger, or paid sessions for legacy data.

    Current partial days compare with the same local time on the matching
    previous day. Historical custom ranges compare complete calendar days.
    Never add both ledgers: that would count the same payment twice.
    """
    if CoinEvent.objects.exists():
        records, stamp, amount = CoinEvent.objects.all(), 'timestamp', 'amount'
        source = 'Recorded coin payments'
    else:
        records = Session.objects.filter(status__in=('active', 'expired', 'paused'))
        stamp, amount, source = 'time_in', 'amount_paid', 'Paid session records'

    def total(first, last, cutoff=None):
        qs = records
        if first:
            qs = qs.filter(**{f'{stamp}__date__gte': first})
        if last:
            qs = qs.filter(**{f'{stamp}__date__lte': last})
        if cutoff:
            qs = qs.filter(**{f'{stamp}__lte': cutoff})
        return qs.aggregate(total=Sum(amount))['total'] or 0

    current = total(start, end, now)
    previous_start = previous_end = None
    if period == 'all':
        title, label = 'All-Time Revenue', 'Cumulative revenue; no previous-period comparison'
    elif period == 'week':
        previous_start, previous_end = start - timedelta(days=7), end - timedelta(days=7)
        title, label = 'Weekly Revenue Growth Benchmark', 'Compared to the same days last week'
    elif period == 'year':
        previous_start = start.replace(year=start.year - 1)
        try:
            previous_end = end.replace(year=end.year - 1)
        except ValueError:  # Feb 29 has no matching day in a non-leap year.
            previous_end = end.replace(year=end.year - 1, day=28)
        title, label = 'Annual Revenue Growth Benchmark', 'Compared to the same year-to-date period last year'
    else:
        span = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=span - 1)
        title = 'Daily Revenue Growth Benchmark' if period == 'today' else (
            '30-Day Revenue Growth Benchmark' if period == 'month' else 'Period Revenue Growth Benchmark')
        label = 'Compared to yesterday' if period == 'today' else f'Compared to previous {span} days'

    previous = None
    growth = None
    if previous_start:
        cutoff = None
        local_now = timezone.localtime(now)
        if end == local_now.date():
            cutoff = local_now.replace(year=previous_end.year, month=previous_end.month, day=previous_end.day)
            label += ' at the same time of day'
        previous = total(previous_start, previous_end, cutoff)
        if previous > 0:
            growth = round((current - previous) / previous * 100, 1)

    return {
        'revenue_growth': growth, 'growth_comparable': growth is not None,
        'growth_title': title, 'growth_label': label,
        'growth_current_revenue': current, 'growth_previous_revenue': previous,
        'growth_source': source,
        'growth_previous_start': previous_start, 'growth_previous_end': previous_end,
    }
