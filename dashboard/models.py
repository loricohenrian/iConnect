"""
Dashboard Models — Announcement, RevenueGoal, ProjectCost, DailyRevenueSummary
"""
from django.db import models
from django.utils import timezone


class Announcement(models.Model):
    """Announcements displayed on the captive portal."""
    message = models.TextField(help_text='Announcement message for students')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Announcement'
        verbose_name_plural = 'Announcements'

    def __str__(self):
        return f'{"[Active]" if self.is_active else "[Inactive]"} {self.message[:50]}'


class RevenueGoal(models.Model):
    """Revenue targets for tracking business performance."""
    PERIOD_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
    ]

    period = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    target_amount = models.PositiveIntegerField(help_text='Target revenue in ₱')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Revenue Goal'
        verbose_name_plural = 'Revenue Goals'

    def __str__(self):
        return f'{self.get_period_display()} Goal: ₱{self.target_amount}'


class ProjectCost(models.Model):
    """Individual cost items for ROI tracking."""
    description = models.CharField(max_length=255, help_text='e.g., "ALLAN H3 Board"')
    amount = models.PositiveIntegerField(help_text='Cost in ₱')
    date_added = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-date_added']
        verbose_name = 'Project Cost'
        verbose_name_plural = 'Project Costs'

    def __str__(self):
        return f'{self.description} — ₱{self.amount}'

    @classmethod
    def total_cost(cls):
        """Return total project cost."""
        result = cls.objects.aggregate(total=models.Sum('amount'))
        return result['total'] or 0


class DailyRevenueSummary(models.Model):
    """Pre-computed daily revenue summary for fast analytics."""
    date = models.DateField(unique=True)
    total_revenue = models.PositiveIntegerField(default=0)
    total_sessions = models.PositiveIntegerField(default=0)
    avg_session_minutes = models.FloatField(default=0)
    peak_hour = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Hour with most sessions (0-23)'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Daily Revenue Summary'
        verbose_name_plural = 'Daily Revenue Summaries'

    def __str__(self):
        return f'{self.date} — ₱{self.total_revenue} ({self.total_sessions} sessions)'


class OperatingExpense(models.Model):
    """Recurring operating expenses (e.g. ISP, Maintenance, Electricity)."""
    PERIOD_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]

    name = models.CharField(max_length=255, help_text='e.g., "Internet (Converge)"')
    amount = models.PositiveIntegerField(help_text='Cost in ₱')
    period = models.CharField(max_length=10, choices=PERIOD_CHOICES, default='monthly')
    date_added = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-date_added']
        verbose_name = 'Operating Expense'
        verbose_name_plural = 'Operating Expenses'

    def __str__(self):
        return f'{self.name} — ₱{self.amount}/{self.period}'

    @classmethod
    def calculate_total_expenses(cls, days_operating):
        """
        Calculate total historical expense incurred over the days operating.
        Converts each expense's period into a daily cost and multiplies by days.
        """
        total = 0.0
        for exp in cls.objects.all():
            if exp.period == 'daily':
                daily_cost = exp.amount
            elif exp.period == 'weekly':
                daily_cost = exp.amount / 7.0
            elif exp.period == 'monthly':
                daily_cost = exp.amount / 30.0
            elif exp.period == 'yearly':
                daily_cost = exp.amount / 365.0
            else:
                daily_cost = 0
            
            total += daily_cost * days_operating
        return round(total, 2)
