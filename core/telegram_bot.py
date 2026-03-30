"""
Telegram Bot Integration
Two-way communication: notifications OUT + commands IN.

Notifications: trade entries, exits, P&L, scan results, errors, daily summaries
Commands: /status, /scan, /positions, /risk, /stop, /start, /balance
"""
import os
import json
import logging
import threading
import time
import requests
from typing import Optional, Dict, Callable, List
from datetime import datetime, timezone
from functools import wraps

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Lightweight Telegram bot using the Bot API directly.
    No python-telegram-bot dependency — just requests.
    """

    def __init__(self, token: str, chat_id: str = None):
        self.token = token
        self.chat_id = chat_id  # Will be auto-detected on first /start
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._commands: Dict[str, Callable] = {}
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None
        self._last_update_id = 0

        # Register built-in commands
        self._register_builtins()

    # ── Sending Messages ────────────────────────

    def send(self, text: str, parse_mode: str = "HTML", chat_id: str = None):
        """Send a message to the configured chat."""
        target = chat_id or self.chat_id
        if not target:
            logger.warning("No chat_id set — cannot send Telegram message")
            return

        # Telegram max message length is 4096
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]

        for chunk in chunks:
            try:
                resp = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": target,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.error(f"Telegram send error: {resp.status_code} {resp.text}")
            except Exception as e:
                logger.error(f"Telegram send failed: {e}")

    def notify_trade_entry(self, epic: str, direction: str, size: float,
                           entry_price: float, stop: float, targets: List[float],
                           pattern: str = "", risk_usd: float = 0):
        """Send trade entry notification."""
        targets_str = " → ".join([f"${t:.2f}" for t in targets])
        rr = abs(targets[0] - entry_price) / abs(entry_price - stop) if targets and abs(entry_price - stop) > 0 else 0

        msg = (
            f"🟢 <b>NEW TRADE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>{direction} {epic}</b>\n"
            f"📊 Pattern: {pattern}\n"
            f"💰 Size: {size} units\n"
            f"🎯 Entry: <b>${entry_price:.2f}</b>\n"
            f"🛑 Stop: ${stop:.2f}\n"
            f"🏁 Targets: {targets_str}\n"
            f"⚖️ R:R: {rr:.1f}:1\n"
            f"💵 Risk: ${risk_usd:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        self.send(msg)

    def notify_trade_exit(self, epic: str, exit_type: str, price: float,
                          pnl: float, remaining: float = 0):
        """Send trade exit notification."""
        emoji = "🟢" if pnl >= 0 else "🔴"
        exit_label = {
            "PARTIAL_EXIT": "PARTIAL EXIT",
            "FULL_EXIT": "FULL EXIT",
            "STOP_LOSS": "STOPPED OUT",
            "TRAILING_STOP": "TRAILING STOP HIT",
        }.get(exit_type, exit_type)

        msg = (
            f"{emoji} <b>{exit_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📉 <b>{epic}</b> @ ${price:.2f}\n"
            f"💰 P&L: <b>${pnl:+.2f}</b>\n"
        )
        if remaining > 0:
            msg += f"📦 Remaining: {remaining} units\n"
        msg += "━━━━━━━━━━━━━━━━━━━"
        self.send(msg)

    def notify_scan_results(self, setups: List[Dict]):
        """Send scan results summary."""
        if not setups:
            return

        msg = f"🔍 <b>SCAN RESULTS</b> ({len(setups)} setups)\n━━━━━━━━━━━━━━━━━━━\n"
        for s in setups[:5]:  # Max 5 in one message
            msg += (
                f"\n<b>{s.get('epic', '?')}</b> [{s.get('pattern', '?')}]\n"
                f"  Score: {s.get('score', 0)} | R:R: {s.get('risk_reward', 0)}\n"
                f"  Entry: ${s.get('entry', 0):.2f} | Stop: ${s.get('stop', 0):.2f}\n"
            )
        msg += "\n━━━━━━━━━━━━━━━━━━━"
        self.send(msg)

    def notify_daily_summary(self, risk_status: Dict, positions: List[Dict]):
        """Send end-of-day summary."""
        rs = risk_status
        msg = (
            f"📊 <b>DAILY SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Capital: <b>${rs.get('capital', 0):.2f}</b>\n"
            f"📈 Daily P&L: ${rs.get('daily_pnl', 0):+.2f}\n"
            f"⚠️ Risk used: ${rs.get('daily_risk_used', 0):.2f} / ${rs.get('max_daily_risk', 0):.2f}\n"
            f"📦 Open positions: {rs.get('open_positions', 0)} / {rs.get('max_positions', 3)}\n"
            f"✅ Can trade: {'Yes' if rs.get('can_trade') else 'No'}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        if positions:
            msg += "\n\n<b>Open Positions:</b>\n"
            for p in positions:
                msg += f"  • {p.get('epic', '?')} @ ${p.get('entry_price', 0):.2f} ({p.get('status', '?')})\n"

        self.send(msg)

    def notify_error(self, error_msg: str):
        """Send error alert."""
        msg = f"🚨 <b>ERROR</b>\n━━━━━━━━━━━━━━━━━━━\n{error_msg}\n━━━━━━━━━━━━━━━━━━━"
        self.send(msg)

    def notify_startup(self, mode: str, balance: float):
        """Send bot startup notification."""
        msg = (
            f"🤖 <b>BOT STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n"
            f"Balance: ${balance:.2f}\n"
            f"Risk/trade: ${balance * 0.02:.2f} (2%)\n"
            f"Daily max: ${balance * 0.05:.2f} (5%)\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Send /help for commands"
        )
        self.send(msg)

    # ── Command Handling ────────────────────────

    def command(self, name: str):
        """Decorator to register a command handler."""
        def decorator(func):
            self._commands[name] = func
            return func
        return decorator

    def register_command(self, name: str, handler: Callable):
        """Register a command handler programmatically."""
        self._commands[name] = handler

    def _register_builtins(self):
        """Register built-in commands."""

        @self.command("start")
        def cmd_start(chat_id, args):
            self.chat_id = str(chat_id)
            self._save_chat_id()
            self.send(
                "🤖 <b>Swing Trading Bot Connected!</b>\n\n"
                "Send /help to see available commands.",
                chat_id=str(chat_id),
            )

        @self.command("help")
        def cmd_help(chat_id, args):
            self.send(
                "<b>📋 Commands:</b>\n\n"
                "/status — Current risk & capital status\n"
                "/positions — Open positions\n"
                "/scan — Run market scan now\n"
                "/balance — Account balance\n"
                "/risk — Risk manager status\n"
                "/stop — Stop the bot\n"
                "/start — Connect this chat\n"
                "/help — This message",
                chat_id=str(chat_id),
            )

    # ── Polling for Commands ────────────────────

    def start_polling(self):
        """Start polling for incoming commands in a background thread."""
        if self._polling:
            return

        self._polling = True
        self._load_chat_id()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("Telegram polling started")

    def stop_polling(self):
        """Stop the polling loop."""
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        logger.info("Telegram polling stopped")

    def _poll_loop(self):
        """Background loop that checks for new messages."""
        while self._polling:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._process_update(update)
            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                time.sleep(1)
            # No sleep needed — long polling already waits for messages

    def _get_updates(self) -> List[Dict]:
        """Fetch new messages from Telegram."""
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={
                    "offset": self._last_update_id + 1,
                    "timeout": 3,
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", [])
        except requests.exceptions.Timeout:
            pass  # Normal for long polling
        except Exception as e:
            logger.error(f"Error fetching Telegram updates: {e}")
        return []

    def _process_update(self, update: Dict):
        """Process a single Telegram update."""
        self._last_update_id = update.get("update_id", self._last_update_id)

        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        if not text or not chat_id:
            return

        # Parse command
        if text.startswith("/"):
            parts = text.split()
            cmd = parts[0][1:].lower().split("@")[0]  # Remove /  and @botname
            args = parts[1:]

            if cmd in self._commands:
                try:
                    self._commands[cmd](chat_id, args)
                except Exception as e:
                    logger.error(f"Command /{cmd} error: {e}")
                    self.send(f"❌ Error: {str(e)}", chat_id=str(chat_id))
            else:
                self.send(
                    f"Unknown command: /{cmd}\nSend /help for available commands.",
                    chat_id=str(chat_id),
                )

    # ── Chat ID Persistence ─────────────────────

    def _save_chat_id(self):
        """Save chat_id to disk so it persists across restarts."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "telegram_chat_id.txt"
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(str(self.chat_id))
        except Exception as e:
            logger.error(f"Failed to save chat_id: {e}")

    def _load_chat_id(self):
        """Load saved chat_id from disk."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "telegram_chat_id.txt"
        )
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    self.chat_id = f.read().strip()
                logger.info(f"Loaded Telegram chat_id: {self.chat_id}")
        except Exception as e:
            logger.error(f"Failed to load chat_id: {e}")
