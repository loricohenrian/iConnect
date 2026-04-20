from django.contrib import admin

from .models import ReportDeliveryLog


@admin.register(ReportDeliveryLog)
class ReportDeliveryLogAdmin(admin.ModelAdmin):
    list_display = (
        "report_type",
        "report_date",
        "status",
        "email_sent",
        "started_at",
        "finished_at",
    )
    list_filter = ("report_type", "status", "email_sent", "report_date")
    search_fields = ("delivered_to", "error_message", "pdf_file_path", "csv_file_path")
