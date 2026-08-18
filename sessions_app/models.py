"""
Session management models.
"""
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Plan(models.Model):
    """WiFi access plan with price and duration."""

    name = models.CharField(max_length=100, help_text='e.g., "\u20b15 Plan"')
    price = models.PositiveIntegerField(help_text="Price in Philippine Peso (integer)")
    duration_minutes = models.PositiveIntegerField(help_text="Duration in minutes")
    speed_limit = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Download speed cap in Mbps",
    )
    speed_limit_upload = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Upload speed cap in Mbps (defaults to download speed if blank)",
    )
    pause_limit = models.PositiveIntegerField(
        default=0,
        help_text="Maximum number of times this plan can be paused (0 = unlimited)",
    )
    pause_duration_limit = models.PositiveIntegerField(
        default=0,
        help_text="Maximum total hours a session can remain paused (0 = unlimited)",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price"]
        verbose_name = "WiFi Plan"
        verbose_name_plural = "WiFi Plans"

    def __str__(self):
        return f"{self.name} - P{self.price} / {self.duration_minutes} min"

    @property
    def duration_display(self):
        """Return a human-readable duration."""
        if self.duration_minutes >= 60:
            hours = self.duration_minutes // 60
            mins = self.duration_minutes % 60
            if mins:
                return f'{hours} hour{"s" if hours > 1 else ""} {mins} mins'
            return f'{hours} hour{"s" if hours > 1 else ""}'
        return f"{self.duration_minutes} mins"

    @property
    def price_per_minute(self):
        """Return the plan price per minute rounded to 2 decimals."""
        if self.duration_minutes <= 0:
            return 0
        return round(self.price / self.duration_minutes, 2)


class SessionGroup(models.Model):
    """A group pass that allows multiple devices to share the same countdown."""
    
    STATUS_CHOICES = [
        ("active", "Active"),
        ("expired", "Expired"),
    ]
    
    group_code = models.CharField(max_length=10, unique=True, help_text="Unique code to join the group")
    max_devices = models.PositiveIntegerField(help_text="Maximum allowed devices")
    total_price = models.PositiveIntegerField(help_text="Total amount paid in ₱")
    duration_minutes = models.PositiveIntegerField()
    time_in = models.DateTimeField(default=timezone.now)
    time_out = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-time_in"]
        verbose_name = "Session Group"
        verbose_name_plural = "Session Groups"

    def __str__(self):
        return f"Group {self.group_code} ({self.status})"

    @property
    def connected_devices_count(self):
        return self.sessions.filter(status__in=["active", "paused"]).count()
        
    def is_full(self):
        return self.connected_devices_count >= self.max_devices


class Session(models.Model):
    """A WiFi session for a connected device."""

    STATUS_CHOICES = [
        ("active", "Active"),
        ("expired", "Expired"),
        ("paused", "Paused"),
    ]

    mac_address = models.CharField(max_length=17, help_text="Format: AA:BB:CC:DD:EE:FF")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="sessions", null=True, blank=True)
    session_group = models.ForeignKey(SessionGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    time_in = models.DateTimeField(default=timezone.now)
    time_out = models.DateTimeField(null=True, blank=True)
    duration_minutes_purchased = models.PositiveIntegerField()
    remaining_minutes = models.FloatField(default=0)
    amount_paid = models.PositiveIntegerField(help_text="Total amount paid in \u20b1")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
    voucher_code = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        unique=True,
        help_text="6-character voucher for session extension",
    )
    bandwidth_used_mb = models.FloatField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_name = models.CharField(max_length=100, null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True, help_text="When session was paused")
    total_paused_seconds = models.FloatField(default=0, help_text="Total seconds spent paused")
    pause_count = models.PositiveIntegerField(default=0, help_text="Number of times paused")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-time_in"]
        verbose_name = "WiFi Session"
        verbose_name_plural = "WiFi Sessions"

    def __str__(self):
        return f"Session {self.id} - {self.mac_address} ({self.status})"

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def time_remaining_seconds(self):
        """Calculate remaining time in seconds from time_in and purchased minutes."""
        if self.session_group:
            if self.session_group.time_out:
                remaining = (self.session_group.time_out - timezone.now()).total_seconds()
                return max(0, remaining)
            return 0

        if self.status == "paused":
            from dashboard.models import SystemSettings
            global_max_pause = SystemSettings.get_settings().global_pause_limit_hours
            max_pause_hours = self.plan.pause_duration_limit if self.plan and self.plan.pause_duration_limit > 0 else global_max_pause
            
            if max_pause_hours > 0 and self.paused_at:
                paused_time = (timezone.now() - self.paused_at).total_seconds()
                max_pause_seconds = max_pause_hours * 3600
                if paused_time > max_pause_seconds:
                    # Treat it as if it automatically resumed exactly when it hit the limit
                    effective_paused_time_this_pause = max_pause_seconds
                    time_since_resume = paused_time - max_pause_seconds
                    
                    elapsed = (timezone.now() - self.time_in).total_seconds() - self.total_paused_seconds - effective_paused_time_this_pause
                    total_seconds = self.duration_minutes_purchased * 60
                    return max(0, total_seconds - elapsed)
                    
            # When paused normally, freeze remaining time at point of pause
            elapsed = (self.paused_at - self.time_in).total_seconds() - self.total_paused_seconds
            total_seconds = self.duration_minutes_purchased * 60
            return max(0, total_seconds - elapsed)
            
        if self.status != "active":
            return 0
        elapsed = (timezone.now() - self.time_in).total_seconds() - self.total_paused_seconds
        total_seconds = self.duration_minutes_purchased * 60
        remaining = total_seconds - elapsed
        return max(0, remaining)

    @property
    def time_remaining_display(self):
        """Return HH:MM:SS format."""
        seconds = int(self.time_remaining_seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def extend_session(self, additional_minutes):
        """Extend the session by adding more time."""
        self.duration_minutes_purchased += additional_minutes
        if self.status == "expired":
            self.status = "active"
            self.time_in = timezone.now()
            self.duration_minutes_purchased = additional_minutes
        self.save()

    def expire_session(self):
        """Mark session as expired and set time_out."""
        self.status = "expired"
        # Calculate exact end time based on purchased duration + pause time
        total_seconds = self.duration_minutes_purchased * 60 + self.total_paused_seconds
        self.time_out = self.time_in + timedelta(seconds=total_seconds)
        self.paused_at = None
        self.save()

    def pause_session(self):
        """Pause the session — freezes timer."""
        if self.status != "active":
            return False
        self.status = "paused"
        self.paused_at = timezone.now()
        self.pause_count += 1
        self.save(update_fields=["status", "paused_at", "pause_count"])
        return True

    def resume_session(self):
        """Resume a paused session — restarts timer."""
        if self.status != "paused" or not self.paused_at:
            return False
        paused_duration = (timezone.now() - self.paused_at).total_seconds()
        self.total_paused_seconds += paused_duration
        self.status = "active"
        self.paused_at = None
        self.save(update_fields=["status", "paused_at", "total_paused_seconds"])
        return True

    @staticmethod
    def generate_voucher_code():
        """Generate a unique 6-character alphanumeric voucher code."""
        chars = string.ascii_uppercase + string.digits
        voucher_length = getattr(settings, "PISONET_VOUCHER_LENGTH", 6)
        while True:
            code = "".join(secrets.choice(chars) for _ in range(voucher_length))
            if not Session.objects.filter(voucher_code=code).exists():
                return code


class CoinEvent(models.Model):
    """Individual coin insertion event from the coin acceptor."""

    DENOMINATION_CHOICES = [
        (1, "\u20b11"),
        (5, "\u20b15"),
        (10, "\u20b110"),
        (20, "\u20b120"),
    ]

    amount = models.PositiveIntegerField(help_text="Total amount from this insertion")
    denomination = models.PositiveIntegerField(
        choices=DENOMINATION_CHOICES,
        help_text="Coin denomination",
    )
    mac_address = models.CharField(
        max_length=17,
        null=True,
        blank=True,
        help_text="Device MAC associated with this payment, when known",
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coin_events",
    )
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Coin Event"
        verbose_name_plural = "Coin Events"

    def __str__(self):
        if self.mac_address:
            return f"Coin {self.amount} for {self.mac_address} at {self.timestamp.strftime('%b %d, %Y %H:%M')}"
        return f"Coin {self.amount} at {self.timestamp.strftime('%b %d, %Y %H:%M')}"


class PurchaseTransaction(models.Model):
    """
    Immutable record of a purchase. Used to accurately attribute revenue 
    to the specific plan that was bought at the time of transaction,
    regardless of whether the session is later extended with a different plan.
    """
    session = models.ForeignKey(
        Session,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchases",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
    )
    amount = models.PositiveIntegerField(help_text="Amount paid in this transaction")
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Purchase Transaction"
        verbose_name_plural = "Purchase Transactions"

    def __str__(self):
        plan_name = self.plan.name if self.plan else "Unknown Plan"
        return f"{plan_name} - ₱{self.amount} at {self.timestamp.strftime('%b %d, %Y %H:%M')}"


class CoinInsertRequest(models.Model):
    """Queue entry for assigning shared-slot coin inserts to one device at a time."""

    PURPOSE_START = "start"
    PURPOSE_CHOICES = [
        (PURPOSE_START, "Start Session"),
    ]

    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    mac_address = models.CharField(max_length=17, help_text="Format: AA:BB:CC:DD:EE:FF")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES, default=PURPOSE_START)
    
    # Standard Plan
    plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coin_insert_requests",
    )
    
    # Group Pass
    is_group_pass = models.BooleanField(default=False)
    group_pass_devices = models.PositiveIntegerField(null=True, blank=True)
    group_pass_duration_minutes = models.PositiveIntegerField(null=True, blank=True)

    expected_amount = models.PositiveIntegerField(default=0, help_text="Amount needed to complete this request")
    credited_amount = models.PositiveIntegerField(default=0, help_text="Current unconsumed credited amount")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Coin Insert Request"
        verbose_name_plural = "Coin Insert Requests"

    def __str__(self):
        return f"{self.mac_address} {self.purpose} ({self.status})"


class WhitelistedDevice(models.Model):
    """Devices that bypass coin payment."""

    mac_address = models.CharField(
        max_length=17,
        unique=True,
        help_text="Format: AA:BB:CC:DD:EE:FF",
    )
    device_name = models.CharField(max_length=100, help_text='e.g., "Admin laptop"')
    added_by = models.CharField(max_length=100, default="admin")
    date_added = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["device_name"]
        verbose_name = "Whitelisted Device"
        verbose_name_plural = "Whitelisted Devices"

    def __str__(self):
        return f"{self.device_name} ({self.mac_address})"


class SuspiciousDevice(models.Model):
    """Tracks suspicious device behavior for operator review and blocking."""

    STATUS_NEW = "new"
    STATUS_BLOCKED = "blocked"
    STATUS_FALSE_POSITIVE = "false_positive"
    STATUS_CLEARED = "cleared"

    STATUS_CHOICES = [
        (STATUS_NEW, "New"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_FALSE_POSITIVE, "False Positive"),
        (STATUS_CLEARED, "Cleared"),
    ]

    mac_address = models.CharField(
        max_length=17,
        unique=True,
        help_text="Format: AA:BB:CC:DD:EE:FF",
    )
    last_ip_address = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=64, default="suspicious_activity")
    evidence = models.TextField(blank=True, default="")
    detection_count = models.PositiveIntegerField(default=1)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW)
    is_blocked = models.BooleanField(default=False)

    first_detected_at = models.DateTimeField(auto_now_add=True)
    last_detected_at = models.DateTimeField(default=timezone.now)
    blocked_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["-last_detected_at"]
        verbose_name = "Suspicious Device"
        verbose_name_plural = "Suspicious Devices"

    def __str__(self):
        return f"{self.mac_address} ({self.get_status_display()})"

    @classmethod
    def record_incident(cls, mac_address, ip_address="", reason="suspicious_activity", evidence=""):
        """Create or update suspicious-device record for this MAC."""
        mac = (mac_address or "").upper().strip()
        if not mac:
            return None

        incident, created = cls.objects.get_or_create(
            mac_address=mac,
            defaults={
                "last_ip_address": ip_address or "",
                "reason": reason,
                "evidence": (evidence or "")[:1000],
                "last_detected_at": timezone.now(),
                "detection_count": 1,
                "status": cls.STATUS_NEW,
                "is_blocked": False,
            },
        )

        if created:
            return incident

        incident.last_detected_at = timezone.now()
        incident.detection_count += 1
        incident.last_ip_address = ip_address or incident.last_ip_address
        incident.reason = reason
        if evidence:
            incident.evidence = evidence[:1000]

        if incident.status in (cls.STATUS_CLEARED, cls.STATUS_FALSE_POSITIVE):
            incident.status = cls.STATUS_NEW
            incident.is_blocked = False
            incident.blocked_at = None
            incident.resolved_at = None
            incident.resolved_by = ""

        incident.save(
            update_fields=[
                "last_detected_at",
                "detection_count",
                "last_ip_address",
                "reason",
                "evidence",
                "status",
                "is_blocked",
                "blocked_at",
                "resolved_at",
                "resolved_by",
            ]
        )
        return incident

    def mark_blocked(self, by=""):
        self.status = self.STATUS_BLOCKED
        self.is_blocked = True
        self.blocked_at = timezone.now()
        self.resolved_at = None
        self.resolved_by = by or self.resolved_by
        self.save(update_fields=["status", "is_blocked", "blocked_at", "resolved_at", "resolved_by"])

    def mark_cleared(self, by=""):
        self.status = self.STATUS_CLEARED
        self.is_blocked = False
        self.resolved_at = timezone.now()
        self.resolved_by = by
        self.save(update_fields=["status", "is_blocked", "resolved_at", "resolved_by"])

    def mark_false_positive(self, by=""):
        self.status = self.STATUS_FALSE_POSITIVE
        self.is_blocked = False
        self.resolved_at = timezone.now()
        self.resolved_by = by
        self.save(update_fields=["status", "is_blocked", "resolved_at", "resolved_by"])




class DeviceProfile(models.Model):
    mac_address = models.CharField(max_length=17, unique=True, help_text="Device MAC Address")
    points = models.PositiveIntegerField(default=0, help_text="Accumulated Gamification Points")
    current_streak = models.PositiveIntegerField(default=0, help_text="Current Daily Connection Streak")
    last_connected_date = models.DateField(null=True, blank=True, help_text="Date of last connection")
    spins_today = models.PositiveIntegerField(default=0, help_text="Number of wheel spins today")
    last_spin_date = models.DateField(null=True, blank=True, help_text="Date of last wheel spin")

    class Meta:
        verbose_name = "Device Profile"
        verbose_name_plural = "Device Profiles"

    def __str__(self):
        return f"{self.mac_address} - Points: {self.points} | Streak: {self.current_streak}"

    @classmethod
    def record_connection(cls, mac_address):
        if not mac_address:
            return None
        from dashboard.models import SystemSettings
        from django.utils import timezone
        from datetime import timedelta

        settings_obj = SystemSettings.get_settings()
        profile, created = cls.objects.get_or_create(mac_address=mac_address)
        today = timezone.now().date()

        if profile.last_connected_date == today:
            return profile

        if profile.last_connected_date == today - timedelta(days=1):
            profile.current_streak += 1
            if settings_obj.enable_spin_wheel:
                profile.points += settings_obj.points_per_streak_day
        else:
            profile.current_streak = 1
            if settings_obj.enable_spin_wheel and not profile.last_connected_date:
                # First time gets points too
                profile.points += settings_obj.points_per_streak_day

        profile.last_connected_date = today
        profile.save()
        return profile

    @classmethod
    def add_spending_points(cls, mac_address, amount_spent):
        if not mac_address or amount_spent <= 0:
            return None
        from dashboard.models import SystemSettings
        settings_obj = SystemSettings.get_settings()
        
        if not settings_obj.enable_spin_wheel or settings_obj.points_per_peso <= 0:
            return None
            
        profile, _ = cls.objects.get_or_create(mac_address=mac_address)
        points_earned = amount_spent * settings_obj.points_per_peso
        profile.points += points_earned
        profile.save(update_fields=['points'])
        return profile

class SpinPrize(models.Model):
    name = models.CharField(max_length=50, help_text="Prize Name (e.g. '30 Mins', 'Try Again')")
    minutes_reward = models.PositiveIntegerField(default=0, help_text="Minutes added to session (0 for Try Again)")
    probability_weight = models.PositiveIntegerField(default=10, help_text="Higher weight = higher chance to win")
    is_active = models.BooleanField(default=True, help_text="Is this prize currently active on the wheel?")

    class Meta:
        verbose_name = "Spin Prize"
        verbose_name_plural = "Spin Prizes"

    def __str__(self):
        return f"{self.name} ({self.minutes_reward}m) - W: {self.probability_weight}"

