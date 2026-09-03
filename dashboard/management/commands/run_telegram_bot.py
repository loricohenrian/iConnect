"""
Management command to run the iConnect Telegram Bot poller service.
Usage: python manage.py run_telegram_bot
"""
import threading
import time
import json
import logging
import urllib.request
import urllib.parse
from django.db import close_old_connections
from django.core.management.base import BaseCommand
from dashboard.telegram_bot import get_telegram_config, handle_telegram_command, send_telegram_message

logger = logging.getLogger(__name__)


def _internet_monitor_loop():
    """Continuously monitors ISP connection every 15s independent of Celery Beat."""
    logger.info("Started background ISP health monitor thread.")
    while True:
        try:
            close_old_connections()
            from sessions_app.tasks import check_internet_status
            check_internet_status()
        except Exception as e:
            logger.error(f"Error in ISP monitor loop: {e}")
        finally:
            close_old_connections()
        time.sleep(15)


class Command(BaseCommand):
    help = "Run the iConnect Telegram bot polling daemon and background ISP monitor"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🤖 Starting iConnect Telegram Bot poller..."))

        # Start background ISP internet monitoring thread
        monitor_thread = threading.Thread(target=_internet_monitor_loop, daemon=True)
        monitor_thread.start()
        self.stdout.write(self.style.SUCCESS("🛡️ Started background ISP monitor thread (15s interval)"))

        cfg = get_telegram_config()
        token = cfg.get("token")
        if not token:
            self.stderr.write(self.style.ERROR("❌ No Telegram bot token configured in SystemSettings!"))
            # Keep thread running even if token not configured so ISP monitor continues
            while True:
                time.sleep(60)
            return

        # Send greeting to admin that the bot service is online
        admin_chat = cfg.get("chat_id")
        if admin_chat:
            send_telegram_message(
                "🟢 *iConnect Bot Service Online!*\nYour Piso WiFi controller is listening for remote commands. Type /help to begin.",
                chat_id=admin_chat
            )

        offset = 0
        poll_url = f"https://api.telegram.org/bot{token}/getUpdates"

        self.stdout.write(self.style.SUCCESS(f"Listening for updates on token {token[:10]}..."))

        while True:
            try:
                params = {"offset": offset, "timeout": 20}
                url = f"{poll_url}?{urllib.parse.urlencode(params)}"
                req = urllib.request.Request(url, headers={"User-Agent": "iConnectBot/1.0"})

                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("ok"):
                            for update in data.get("result", []):
                                update_id = update.get("update_id", 0)
                                offset = max(offset, update_id + 1)

                                message = update.get("message")
                                if message and "text" in message:
                                    sender = message.get("from", {})
                                    sender_id = sender.get("id")
                                    sender_name = sender.get("first_name", "Operator")
                                    text = message.get("text", "")

                                    self.stdout.write(f"Received from {sender_name} ({sender_id}): {text}")
                                    try:
                                        handle_telegram_command(text, sender_id, sender_name)
                                    except Exception as cmd_err:
                                        logger.error(f"Error processing command '{text}': {cmd_err}", exc_info=True)
                                        self.stderr.write(self.style.ERROR(f"Command error: {cmd_err}"))
                                        send_telegram_message(f"⚠️ Error executing `{text}`: {cmd_err}", chat_id=sender_id)

            except urllib.error.HTTPError as he:
                if he.code == 409:
                    self.stdout.write(self.style.WARNING("Conflict: another bot instance is polling. Waiting 5s..."))
                    time.sleep(5)
                else:
                    self.stdout.write(self.style.WARNING(f"HTTP Error {he.code}: {he}. Waiting 5s..."))
                    time.sleep(5)
            except Exception as e:
                # Network down or timeout — retry gracefully
                time.sleep(3)
