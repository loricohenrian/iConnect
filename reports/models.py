"""
Report models for automated generation and delivery audit logs.
"""
from django.db import models


class ReportDeliveryLog(models.Model):
    """Track automated report generation and delivery status."""

    REPORT_TYPE_CHOICES = [
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    report_type = models.CharField(max_length=10, choices=REPORT_TYPE_CHOICES, default="daily")
    report_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    pdf_file_path = models.CharField(max_length=500, blank=True)
    csv_file_path = models.CharField(max_length=500, blank=True)
    delivered_to = models.TextField(blank=True)
    email_sent = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Report Delivery Log"
        verbose_name_plural = "Report Delivery Logs"

    def __str__(self):
        return f"{self.report_type.title()} report {self.report_date} [{self.status}]"
