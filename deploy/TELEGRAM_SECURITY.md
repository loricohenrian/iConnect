# Reconnect Telegram after token exposure

1. In verified `@BotFather`, revoke the exposed token and select your bot.
   A token shown in a chat, screenshot, source file, or Git history must not be reused.
2. Check Telegram Settings > Devices for unknown sessions, and enable two-step
   verification. Profile tampering alone does not prove your personal account or
   Orange Pi was compromised.
3. Open the admin dashboard privately over Tailscale or another trusted connection.
   In Settings > Integrations, enter the fresh token directly and your positive
   personal Telegram user ID. Do not send the token to an assistant, commit it,
   or include it in terminal commands, logs, or screenshots.
4. Enable the Telegram toggle, save, and use Test Connection. Blank token input
   preserves the saved token; Remove the saved token explicitly clears it.
5. After these settings are verified, an operator can enable/start the existing
   `iconnect-telegram` service. The security deployment intentionally leaves that
   service stopped/disabled until credentials are configured.

Only fresh messages from the configured private admin chat execute commands.
Missing credentials, disabled settings, group messages, and unknown senders are
rejected. The poller reloads settings and does not log tokens, command bodies,
or raw API exceptions. Notification/document delivery is limited to the admin.

The `/backup` export omits the bot token. Set it privately after restoring a backup.
Local operational backups may still contain credentials and must remain private.

Removing a token from the latest code does not erase earlier Git commits or
screenshots. Revocation is required; no history rewrite or force-push is performed.
Restoring the bot's display name/photo/bio is a separate BotFather action and is
not performed by this deployment. No existing webhook is deleted automatically.
