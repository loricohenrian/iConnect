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


class SystemSettings(models.Model):
    """Singleton model for global system settings."""
    # Networking
    enable_anti_tethering = models.BooleanField(
        default=False, 
        help_text="Block hotspot sharing by fixing TTL to 1"
    )
    enable_sqm = models.BooleanField(
        default=False, 
        help_text="Enable CAKE Smart Queue Management for anti-bufferbloat"
    )
    isp_download_speed = models.PositiveIntegerField(
        default=100, 
        help_text="Total ISP Download Speed (Mbps)"
    )
    isp_upload_speed = models.PositiveIntegerField(
        default=100, 
        help_text="Total ISP Upload Speed (Mbps)"
    )

    # General / UI
    enable_dark_mode = models.BooleanField(
        default=False,
        help_text="Force dark mode for the admin dashboard"
    )
    max_concurrent_sessions = models.PositiveIntegerField(
        default=20,
        help_text="Maximum allowed simultaneous connected users"
    )
    global_pause_limit_hours = models.PositiveIntegerField(
        default=24,
        help_text="Global fallback max pause duration in hours (0 = unlimited)"
    )

    # Network & Automation Features
    enable_internet_check = models.BooleanField(
        default=True,
        help_text="Auto-disable coin insertion and auto-pause sessions when the ISP/Internet is down"
    )
    enable_auto_pause_resume = models.BooleanField(
        default=False,
        help_text="Automatically pause sessions when disconnected, and resume when reconnected"
    )
    auto_pause_timeout_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Seconds device must be unreachable before auto-pausing (Default: 300)"
    )
    insert_coin_countdown_seconds = models.PositiveIntegerField(
        default=120,
        help_text="Seconds before the coin slot auto-cancels if no coins are inserted (Default: 120)"
    )

    # Gamification
    enable_spin_wheel = models.BooleanField(
        default=False,
        help_text="Enable Spin the Wheel game for users"
    )
    spin_cost_points = models.PositiveIntegerField(
        default=10,
        help_text="Points required to spin the wheel"
    )
    daily_spin_limit = models.PositiveIntegerField(
        default=3,
        help_text="Maximum allowed spins per day per device"
    )
    points_per_streak_day = models.PositiveIntegerField(
        default=5,
        help_text="Points awarded for connecting on consecutive days"
    )
    points_per_peso = models.PositiveIntegerField(
        default=1,
        help_text="Points awarded for every ₱1 spent"
    )

    # Family / Group Pass
    enable_family_pass = models.BooleanField(
        default=False,
        help_text="Enable the Family/Group Pass feature"
    )
    family_pass_base_rate = models.PositiveIntegerField(
        default=10,
        help_text="Base rate for 1 device per hour (₱)"
    )
    family_pass_device_rate = models.PositiveIntegerField(
        default=5,
        help_text="Rate per additional device per hour (₱)"
    )
    family_pass_max_devices = models.PositiveIntegerField(
        default=6,
        help_text="Maximum devices allowed in a single Family Pass group"
    )
    family_pass_speed_limit = models.FloatField(
        default=5.0,
        help_text="Download speed limit per device in a Family Pass (Mbps)"
    )
    family_pass_speed_limit_upload = models.FloatField(
        default=5.0,
        help_text="Upload speed limit per device in a Family Pass (Mbps)"
    )
    group_code_expiry_hours = models.PositiveIntegerField(
        default=24,
        help_text="Hours after purchase before a group code can no longer be redeemed (0 = no expiry)"
    )

    # Telegram Bot Integration
    enable_telegram_bot = models.BooleanField(
        default=True,
        help_text="Enable Telegram bot for remote management and alerts"
    )
    telegram_bot_token = models.CharField(
        max_length=150,
        default="8946483111:AAEQBhy1vOqLFPdKIXjInvGjNrofI3TqgZg",
        blank=True,
        help_text="Telegram Bot Token from @BotFather"
    )
    telegram_admin_chat_id = models.CharField(
        max_length=50,
        default="6261306648",
        blank=True,
        help_text="Authorized Telegram Admin Chat ID"
    )
    telegram_notify_tickets = models.BooleanField(
        default=True,
        help_text="Send alerts when customer reports an issue ticket"
    )
    telegram_notify_isp_down = models.BooleanField(
        default=True,
        help_text="Send alert when ISP internet drops"
    )
    telegram_notify_daily_summary = models.BooleanField(
        default=True,
        help_text="Send daily midnight sales summary"
    )

    class Meta:
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"

    def __str__(self):
        return "Global System Settings"

    def save(self, *args, **kwargs):
        # Ensure only one instance exists
        if not self.pk and SystemSettings.objects.exists():
            return
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Get the singleton instance, creating it if it doesn't exist."""
        obj, created = cls.objects.get_or_create(id=1)
        return obj


class IssueReport(models.Model):
    """Customer reports and issue tickets submitted from the captive portal."""
    CATEGORY_CHOICES = [
        ('coin_stuck', 'Coin Stuck / Not Credited'),
        ('no_internet', 'No Internet / Slow Speed'),
        ('timer_issue', 'Timer / Session Issue'),
        ('group_pass', 'Group Pass Problem'),
        ('other', 'Other Issue / Feedback'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('resolved', 'Resolved'),
    ]

    mac_address = models.CharField(max_length=17, blank=True, null=True, help_text="Device MAC address")
    contact_info = models.CharField(max_length=100, blank=True, help_text="Optional Contact (Phone, Name, or Messenger)")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='other')
    message = models.TextField(help_text="Issue description from customer")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_notes = models.TextField(blank=True, help_text="Operator resolution notes")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Issue Report'
        verbose_name_plural = 'Issue Reports'

    def __str__(self):
        return f"[{self.get_status_display()}] {self.get_category_display()} ({self.mac_address or 'No MAC'})"

