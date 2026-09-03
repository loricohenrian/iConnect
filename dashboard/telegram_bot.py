"""
iConnect Telegram Bot Integration
Provides remote operations, status checks, earnings monitoring, and real-time alerts.
Uses pure Python standard library (urllib.request) — zero external pip dependencies needed.
"""
import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum

logger = logging.getLogger(__name__)


def get_telegram_config():
    """Retrieve Telegram credentials from SystemSettings."""
    try:
        from dashboard.models import SystemSettings
        s = SystemSettings.get_settings()
        return {
            'enabled': s.enable_telegram_bot,
            'token': s.telegram_bot_token,
            'chat_id': str(s.telegram_admin_chat_id).strip(),
            'notify_tickets': s.telegram_notify_tickets,
            'notify_isp_down': s.telegram_notify_isp_down,
            'notify_daily_summary': s.telegram_notify_daily_summary,
        }
    except Exception as e:
        logger.error(f"Error reading telegram config: {e}")
        return {
            'enabled': True,
            'token': '8946483111:AAEQBhy1vOqLFPdKIXjInvGjNrofI3TqgZg',
            'chat_id': '6261306648',
            'notify_tickets': True,
            'notify_isp_down': True,
            'notify_daily_summary': True,
        }


def send_telegram_message(text, chat_id=None, parse_mode="Markdown"):
    """
    Send a message to the authorized Telegram admin.
    Returns True if sent successfully, False otherwise.
    """
    cfg = get_telegram_config()
    token = cfg.get('token')
    target_chat = chat_id or cfg.get('chat_id')

    if not token or not target_chat:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram sendMessage failed: {e}")
        return False


def send_telegram_document(file_path, caption=None, chat_id=None):
    """
    Send a document file (e.g. database backup) to Telegram.
    Uses multipart/form-data via urllib.
    """
    cfg = get_telegram_config()
    token = cfg.get('token')
    target_chat = chat_id or cfg.get('chat_id')

    if not token or not target_chat or not os.path.exists(file_path):
        return False

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(file_path)

    body = []
    body.append(f"--{boundary}".encode("utf-8"))
    body.append(f'Content-Disposition: form-data; name="chat_id"'.encode("utf-8"))
    body.append(b"")
    body.append(str(target_chat).encode("utf-8"))

    if caption:
        body.append(f"--{boundary}".encode("utf-8"))
        body.append(f'Content-Disposition: form-data; name="caption"'.encode("utf-8"))
        body.append(b"")
        body.append(caption.encode("utf-8"))

    body.append(f"--{boundary}".encode("utf-8"))
    body.append(f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode("utf-8"))
    body.append(b"Content-Type: application/octet-stream")
    body.append(b"")
    with open(file_path, "rb") as f:
        body.append(f.read())

    body.append(f"--{boundary}--".encode("utf-8"))
    body.append(b"")

    payload = b"\r\n".join(body)

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(payload))
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram sendDocument failed: {e}")
        return False


def handle_telegram_command(command_text, sender_id, sender_name="User"):
    """
    Route and process bot commands sent by the authorized user.
    """
    cfg = get_telegram_config()
    authorized_id = str(cfg.get('chat_id')).strip()
    current_sender_id = str(sender_id).strip()

    # Security check: Ignore unauthorized users
    if authorized_id and current_sender_id != authorized_id:
        logger.warning(f"Unauthorized Telegram command from {current_sender_id}: {command_text}")
        send_telegram_message(
            "🔒 *Access Denied*\nYou are not authorized to control this Piso WiFi node.",
            chat_id=sender_id
        )
        return

    cmd = command_text.strip().split()[0].lower() if command_text else ""
    if "@" in cmd:
        cmd = cmd.split("@")[0]

    from sessions_app.models import Session, CoinEvent
    from dashboard.models import IssueReport

    # Always use local Philippine Time (Asia/Manila)
    now = timezone.localtime(timezone.now())

    if cmd in ("/start", "/help"):
        msg = (
            f"👋 *Hello {sender_name}!*\n"
            f"Welcome to your *iConnect Piso WiFi Controller* 🍊📶\n\n"
            f"Here are the available remote commands:\n\n"
            f"💰 */revenue* — Today's earnings & monthly sales\n"
            f"🟢 */status* — System health, CPU temp, ISP state\n"
            f"👥 */users* — Live connected students & timers\n"
            f"⏸ */pauseall* — Emergency pause all active sessions\n"
            f"▶️ */resumeall* — Resume all paused sessions\n"
            f"📋 */tickets* — Open customer support issues\n"
            f"📁 */backup* — Send latest database backup directly\n\n"
            f"⚡ _Fast, secure & always connected to your Orange Pi!_"
        )
        send_telegram_message(msg, chat_id=sender_id)

    elif cmd == "/revenue":
        today_date = now.date()
        month_start_date = today_date.replace(day=1)

        # 1. Hardware Coin Box Sales (exact cash inserted)
        today_coins = CoinEvent.objects.filter(timestamp__date=today_date)
        today_rev = today_coins.aggregate(total=Sum('amount'))['total'] or 0
        today_coin_count = today_coins.count()

        # Fallback to Session amount_paid if no CoinEvent recorded
        today_sessions = Session.objects.filter(time_in__date=today_date)
        if today_rev == 0:
            today_rev = today_sessions.aggregate(total=Sum('amount_paid'))['total'] or 0
        today_vends = today_sessions.count()

        # 2. Month-to-date sales
        month_coins = CoinEvent.objects.filter(
            timestamp__date__gte=month_start_date,
            timestamp__date__lte=today_date
        )
        month_rev = month_coins.aggregate(total=Sum('amount'))['total'] or 0
        if month_rev == 0:
            month_rev = Session.objects.filter(
                time_in__date__gte=month_start_date,
                time_in__date__lte=today_date
            ).aggregate(total=Sum('amount_paid'))['total'] or 0
        month_vends = Session.objects.filter(
            time_in__date__gte=month_start_date,
            time_in__date__lte=today_date
        ).count()

        # 3. Lifetime Total
        lifetime_coins = CoinEvent.objects.aggregate(total=Sum('amount'))['total'] or 0
        if lifetime_coins == 0:
            lifetime_coins = Session.objects.aggregate(total=Sum('amount_paid'))['total'] or 0

        active_users_now = Session.objects.filter(status='active').count()

        msg = (
            f"💰 *iConnect Revenue Report*\n"
            f"📅 _{now.strftime('%A, %B %d, %Y - %I:%M %p')} (PST)_\n\n"
            f"💵 *Today's Earnings:* `₱{today_rev:,.2f}`\n"
            f"🎟 *Today's Vends:* `{today_vends} sessions` ({today_coin_count} coins inserted)\n\n"
            f"📈 *This Month ({now.strftime('%B')}):* `₱{month_rev:,.2f}`\n"
            f"📦 *Month Vends:* `{month_vends} sessions`\n\n"
            f"🪙 *Total Lifetime Revenue:* `₱{lifetime_coins:,.2f}`\n"
            f"👥 *Active Right Now:* `{active_users_now} users`"
        )
        send_telegram_message(msg, chat_id=sender_id)

    elif cmd == "/status":
        active_count = Session.objects.filter(status='active').count()
        paused_count = Session.objects.filter(status='paused').count()

        temp_str = "N/A"
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                celsius = round(int(f.read().strip()) / 1000.0, 1)
                temp_str = f"{celsius}°C"
                if celsius < 55:
                    temp_str += " 🟢 (Cool)"
                elif celsius < 70:
                    temp_str += " 🟡 (Warm)"
                else:
                    temp_str += " 🔴 (Hot!)"
        except Exception:
            temp_str = "Running (Online)"

        isp_ok = True
        try:
            from django.core.cache import cache
            isp_ok = cache.get("internet_status_ok", True)
        except Exception:
            pass
        isp_status = "🟢 ONLINE" if isp_ok else "🔴 OFFLINE"

        max_slots = getattr(settings, "PISONET_MAX_CONCURRENT_SESSIONS", 50)

        msg = (
            f"🟢 *iConnect Node Status*\n"
            f"📡 *System:* Online & Operating\n\n"
            f"🌐 *ISP Internet:* {isp_status}\n"
            f"👥 *Active Sessions:* `{active_count} / {max_slots} slots`\n"
            f"⏸ *Paused Sessions:* `{paused_count}`\n"
            f"🌡 *CPU Temperature:* `{temp_str}`\n"
            f"🕒 *Server Time:* `{now.strftime('%I:%M:%S %p')} (PST)`"
        )
        send_telegram_message(msg, chat_id=sender_id)

    elif cmd == "/users":
        active_sessions = Session.objects.filter(status='active').order_by('-time_in')[:15]
        if not active_sessions.exists():
            send_telegram_message("👥 *Active Users:* None at the moment (0 active).", chat_id=sender_id)
            return

        lines = [f"👥 *Active Users ({active_sessions.count()} online):*\n"]
        for idx, s in enumerate(active_sessions, 1):
            remaining_mins = max(0, int(s.time_remaining_seconds / 60))
            plan_name = s.plan.name if s.plan else "Custom"
            device = s.device_name or s.mac_address[-8:]
            lines.append(f"{idx}. `{device}` — *{remaining_mins}m left* ({plan_name})")

        send_telegram_message("\n".join(lines), chat_id=sender_id)

    elif cmd == "/pauseall":
        active_sessions = Session.objects.filter(status='active')
        count = active_sessions.count()
        if count == 0:
            send_telegram_message("ℹ️ No active sessions to pause.", chat_id=sender_id)
            return

        for s in active_sessions:
            s.pause_session()
            try:
                from sessions_app import iptables
                iptables.block_device(s.mac_address)
            except Exception:
                pass

        send_telegram_message(f"⏸ *Emergency Pause Applied!*\nPaused `{count}` active student session(s). Timers are frozen.", chat_id=sender_id)

    elif cmd == "/resumeall":
        paused_sessions = Session.objects.filter(status='paused')
        count = paused_sessions.count()
        if count == 0:
            send_telegram_message("ℹ️ No paused sessions to resume.", chat_id=sender_id)
            return

        for s in paused_sessions:
            s.resume_session()
            try:
                from sessions_app import iptables
                iptables.allow_device(s.mac_address)
            except Exception:
                pass

        send_telegram_message(f"▶️ *Sessions Resumed!*\nResumed `{count}` session(s). Internet connection restored.", chat_id=sender_id)

    elif cmd == "/tickets":
        pending_tickets = IssueReport.objects.filter(status='pending').order_by('-created_at')[:5]
        if not pending_tickets.exists():
            send_telegram_message("✅ *Customer Support:* No open tickets! All issues resolved.", chat_id=sender_id)
            return

        lines = [f"🚨 *Open Tickets ({pending_tickets.count()} pending):*\n"]
        for t in pending_tickets:
            time_ago = t.created_at.strftime('%I:%M %p')
            lines.append(f"• *Ticket #{t.id}* ({t.get_category_display()})\n  _{t.message}_\n  Contact: `{t.contact_info or 'None'}` · {time_ago}\n")

        send_telegram_message("\n".join(lines), chat_id=sender_id)

    elif cmd == "/backup":
        import io
        import gzip
        import tempfile
        from django.core.management import call_command

        send_telegram_message("📦 Generating database backup (JSON dump)...", chat_id=sender_id)

        timestamp_str = now.strftime('%Y%m%d_%H%M%S')
        temp_dir = tempfile.gettempdir()
        backup_file = os.path.join(temp_dir, f"iconnect_backup_{timestamp_str}.json.gz")

        try:
            buf = io.StringIO()
            call_command(
                'dumpdata',
                stdout=buf,
                exclude=['contenttypes', 'auth.permission'],
            )
            raw_bytes = buf.getvalue().encode('utf-8')
            with gzip.open(backup_file, 'wb') as f:
                f.write(raw_bytes)

            caption = (
                f"💾 *iConnect Database Backup*\n"
                f"📅 {now.strftime('%Y-%m-%d %I:%M %p')} (PST)\n"
                f"📦 Size: {len(raw_bytes)/1024:.1f} KB (Compressed)"
            )
            success = send_telegram_document(backup_file, caption=caption, chat_id=sender_id)
            if not success:
                send_telegram_message("❌ Failed to send backup document to Telegram.", chat_id=sender_id)
        except Exception as e:
            logger.error(f"Backup generation failed: {e}")
            send_telegram_message(f"❌ Backup error: {e}", chat_id=sender_id)
        finally:
            if os.path.exists(backup_file):
                try:
                    os.remove(backup_file)
                except Exception:
                    pass

    else:
        send_telegram_message(
            f"❓ Unknown command: `{cmd}`\nType /help to see the list of available commands.",
            chat_id=sender_id
        )

    else:
        send_telegram_message(
            f"❓ Unknown command: `{cmd}`\nType /help to see the list of available commands.",
            chat_id=sender_id
        )
