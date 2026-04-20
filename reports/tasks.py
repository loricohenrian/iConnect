"""Celery tasks for automated report generation and delivery."""
from pathlib import Path
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .generator import generate_csv_report, generate_report
from .models import ReportDeliveryLog


logger = logging.getLogger(__name__)


def _report_output_dir(report_date):
    """Return output directory for daily report artifacts."""
    output_dir = Path(settings.MEDIA_ROOT) / "reports" / "daily" / report_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _save_bytes_to_file(content, file_path):
    """Persist binary/text bytes to disk and return string path."""
    file_path.write_bytes(content)
    return str(file_path)


@shared_task
def generate_and_deliver_daily_report():
    """
    Generate daily PDF/CSV reports, persist artifacts, and optionally email them.
    Designed to run from Celery Beat at the end of each day.
    """
    today = timezone.localdate()
    log = ReportDeliveryLog.objects.create(
        report_type="daily",
        report_date=today,
        status="running",
    )

    try:
        pdf_response = generate_report(report_type="daily", period="today")
        csv_response = generate_csv_report(report_type="daily", period="today")

        pdf_content = bytes(pdf_response.content)
        csv_content = bytes(csv_response.content)

        output_dir = _report_output_dir(today)
        timestamp = timezone.now().strftime("%H%M%S")
        pdf_name = f"iconnect_daily_{today.isoformat()}_{timestamp}.pdf"
        csv_name = f"iconnect_daily_{today.isoformat()}_{timestamp}.csv"

        pdf_path = _save_bytes_to_file(pdf_content, output_dir / pdf_name)
        csv_path = _save_bytes_to_file(csv_content, output_dir / csv_name)

        recipients = getattr(settings, "PISONET_DAILY_REPORT_RECIPIENTS", [])
        send_email = getattr(settings, "PISONET_DAILY_REPORT_SEND_EMAIL", False)
        email_sent = False
        delivered_to = ""

        if send_email and recipients:
            subject = f"iConnect Daily Report - {today.isoformat()}"
            body = (
                "Attached are the iConnect daily reports.\n\n"
                f"Date: {today.isoformat()}\n"
                f"Generated at: {timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )

            email = EmailMessage(
                subject=subject,
                body=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                to=recipients,
            )
            email.attach(pdf_name, pdf_content, "application/pdf")
            email.attach(csv_name, csv_content, "text/csv")
            email.send(fail_silently=False)

            email_sent = True
            delivered_to = ", ".join(recipients)

        log.status = "success"
        log.pdf_file_path = pdf_path
        log.csv_file_path = csv_path
        log.email_sent = email_sent
        log.delivered_to = delivered_to
        log.finished_at = timezone.now()
        log.error_message = ""
        log.save(update_fields=[
            "status",
            "pdf_file_path",
            "csv_file_path",
            "email_sent",
            "delivered_to",
            "finished_at",
            "error_message",
        ])

        logger.info("Daily report pipeline complete for %s", today.isoformat())
        return {
            "status": "success",
            "report_date": today.isoformat(),
            "pdf_file_path": pdf_path,
            "csv_file_path": csv_path,
            "email_sent": email_sent,
            "delivered_to": delivered_to,
        }
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error_message", "finished_at"])
        logger.exception("Daily report pipeline failed for %s", today.isoformat())
        return {
            "status": "failed",
            "report_date": today.isoformat(),
            "error": str(exc),
        }


