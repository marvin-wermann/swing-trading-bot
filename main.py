#!/usr/bin/env python3
"""
Swing Trading Bot - Main Entry Point
Capital.com REST API | Stocks & Crypto CFDs | Telegram Control

Usage:
  python main.py                    # Run the bot (continuous)
  python main.py --scan             # One-time scan only
  python main.py --status           # Show current status
  python main.py --demo             # Force demo mode

Risk Profile: 2% per trade, 5% daily max
"""
import os
import sys
import time
import json
import signal
import logging
import argparse
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from config.settings import (
    RiskProfile, SwingStrategyConfig, WatchlistConfig, WebhookConfig,
    BASE_URL, CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD, CAPITAL_ACCOUNT_ID,
    USE_DEMO, LOG_DIR, LOG_LEVEL,
)
from core.api_client import CapitalComClient
from core.risk_manager import RiskManager
from core.position_manager import PositionManager
from core.telegram_bot import TelegramBot
from core.webhook_receiver import WebhookReceiver, TelegramSignalParser
from core.signal_executor import SignalExecutor
from strategies.swing_breakout import SwingBreakoutStrategy
from utils.logger import setup_logging

logger = logging.getLogger(__name__)

# Graceful shutdown
_running = True
_telegram: TelegramBot = None

def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received — finishing current cycle...")
    _running = False
    if _telegram:
        _telegram.send("🛑 <b>BOT SHUTTING DOWN</b>\nReceived stop signal.")
        _telegram.stop_polling()

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def setup_telegram(strategy: SwingBreakoutStrategy, api: CapitalComClient) -> TelegramBot:
    """Initialize Telegram bot with command handlers."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram notifications disabled")
        return None

    tg = TelegramBot(token)

    # Register strategy commands
    @tg.command("status")
    def cmd_status(chat_id, args):
        from datetime import timezone, timedelta
        status = strategy.get_status()
        rs = status["risk_status"]

        # Current time in CAT (UTC+2)
        cat_tz = timezone(timedelta(hours=2))
        now_cat = datetime.now(cat_tz)
        time_str = now_cat.strftime("%H:%M CAT")

        # Determine scan window status
        now_utc = datetime.now(timezone.utc)
        scan_hours = [16, 17, 18, 19]  # 12-3 PM ET = 16-19 UTC
        if now_utc.hour in scan_hours and now_utc.weekday() < 5:
            session_status = "Midday scan (12-4 PM ET)"
            trading_icon = "🟢"
            trading_text = "SCANNING"
        elif now_utc.weekday() >= 5:
            session_status = "Weekend (markets closed)"
            trading_icon = "🔴"
            trading_text = "PAUSED"
        else:
            session_status = "Outside scan window"
            trading_icon = "🟡"
            trading_text = "MONITORING"

        # Use cached risk manager capital (no slow API call)
        account_id = api.account_id or "default"
        balance = rs["capital"]
        equity = balance

        # Open positions summary
        positions = status.get("open_positions", [])
        pos_count = len(positions)

        msg = (
            f"📊 <b>Bot Status</b> ({time_str})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Trading: {trading_icon} {trading_text}\n"
            f"Strategy: swing_breakout\n"
            f"Session: {session_status}\n"
            f"Account: {account_id}\n"
            f"Balance: <b>${balance:.2f}</b>\n"
            f"Equity: ${equity:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Daily P&L: ${rs['daily_pnl']:+.2f}\n"
            f"⚠️ Risk: ${rs['daily_risk_used']:.2f} / ${rs['max_daily_risk']:.2f}\n"
            f"📦 Positions: {pos_count} / {rs['max_positions']}\n"
            f"🔍 Watchlist: {len(strategy.watchlist.all_epics)} symbols\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        tg.send(msg, chat_id=str(chat_id))

    @tg.command("positions")
    def cmd_positions(chat_id, args):
        status = strategy.get_status()
        positions = status["open_positions"]
        if not positions:
            tg.send("📦 No open positions.", chat_id=str(chat_id))
            return

        msg = f"📦 <b>OPEN POSITIONS</b> ({len(positions)})\n━━━━━━━━━━━━━━━━━━━\n"
        for p in positions:
            msg += (
                f"\n<b>{p['epic']}</b> ({p['direction']})\n"
                f"  Entry: ${p['entry_price']:.2f}\n"
                f"  Size: {p['remaining_size']} / {p['initial_size']}\n"
                f"  Stop: ${p['stop_price']:.2f}\n"
                f"  Status: {p['status']}\n"
            )
        msg += "\n━━━━━━━━━━━━━━━━━━━"
        tg.send(msg, chat_id=str(chat_id))

    @tg.command("scan")
    def cmd_scan(chat_id, args):
        tg.send("🔍 Running market scan...", chat_id=str(chat_id))
        try:
            summary = strategy.run_cycle()
            status = strategy.get_status()
            setups = status.get("pending_setups", {})

            if setups:
                tg.notify_scan_results([s for s in setups.values()])
            else:
                tg.send("🔍 No qualifying setups found.", chat_id=str(chat_id))

            msg = (
                f"Scan complete: {summary.get('setups_found', 0)} setups, "
                f"{summary.get('entries_taken', 0)} entries"
            )
            tg.send(msg, chat_id=str(chat_id))
        except Exception as e:
            tg.send(f"❌ Scan error: {str(e)}", chat_id=str(chat_id))

    @tg.command("balance")
    def cmd_balance(chat_id, args):
        try:
            balance = api.get_account_balance()
            tg.send(f"💰 Account balance: <b>${balance:.2f}</b>", chat_id=str(chat_id))
        except Exception as e:
            tg.send(f"❌ Error fetching balance: {str(e)}", chat_id=str(chat_id))

    @tg.command("risk")
    def cmd_risk(chat_id, args):
        rs = strategy.risk.get_status()
        msg = (
            f"⚖️ <b>RISK STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Max risk/trade: ${rs['max_risk_per_trade']:.2f}\n"
            f"Max daily risk: ${rs['max_daily_risk']:.2f}\n"
            f"Used today: ${rs['daily_risk_used']:.2f}\n"
            f"Remaining: ${rs['remaining_daily_risk']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        tg.send(msg, chat_id=str(chat_id))

    @tg.command("stop")
    def cmd_stop(chat_id, args):
        global _running
        tg.send("🛑 <b>Stopping bot...</b>\nPositions remain open.", chat_id=str(chat_id))
        _running = False

    return tg


def setup_webhook(api: CapitalComClient, risk_mgr: RiskManager,
                  pos_mgr: PositionManager, tg: TelegramBot = None) -> WebhookReceiver:
    """Initialize webhook receiver and signal executor."""
    webhook_config = WebhookConfig()

    if not webhook_config.enabled:
        logger.info("Webhook receiver disabled (WEBHOOK_ENABLED=false)")
        return None

    if not webhook_config.secret_token:
        logger.warning(
            "WEBHOOK_SECRET not set! Webhook receiver will accept ALL requests.\n"
            "Set WEBHOOK_SECRET in .env for production use."
        )

    # Create signal executor (bridges signals into risk/position pipeline)
    executor = SignalExecutor(api, risk_mgr, pos_mgr, telegram=tg)

    # Create webhook receiver
    webhook = WebhookReceiver(
        secret_token=webhook_config.secret_token,
        port=webhook_config.port,
        host=webhook_config.host,
    )
    webhook.on_signal(executor.execute)

    # Register /trade command on Telegram
    if tg:
        @tg.command("trade")
        def cmd_trade(chat_id, args):
            """
            /trade AAPL buy 192.50 stop 185 tp 200
            /trade BTC sell stop 68000 tp 65000
            """
            if not args:
                tg.send(
                    "📝 <b>Trade Format:</b>\n"
                    "<code>/trade AAPL buy 192 stop 185 tp 200</code>\n"
                    "<code>/trade BTC sell stop 68000 tp 65000</code>\n"
                    "<code>/trade NVDA long 880 stop 850 tp 920 tp 950</code>\n\n"
                    "Use 0 for entry price = market order.\n"
                    "Multiple tp values create quarter-exit targets.",
                    chat_id=str(chat_id),
                )
                return

            raw_text = " ".join(args)
            signal = TelegramSignalParser.parse(raw_text)

            if signal is None:
                tg.send(
                    "❌ Could not parse trade command.\n"
                    "Format: /trade EPIC BUY/SELL [entry] stop PRICE tp PRICE",
                    chat_id=str(chat_id),
                )
                return

            # Confirm before executing
            targets_str = " → ".join([f"${t:.2f}" for t in signal.targets]) if signal.targets else "none"
            entry_str = f"${signal.entry_price:.2f}" if signal.entry_price > 0 else "MARKET"
            tg.send(
                f"🔔 <b>Executing trade signal...</b>\n"
                f"Epic: {signal.epic} | Dir: {signal.direction}\n"
                f"Entry: {entry_str} | Stop: ${signal.stop_price:.2f}\n"
                f"Targets: {targets_str}",
                chat_id=str(chat_id),
            )

            result = executor.execute(signal)

            if result.get("executed"):
                tg.send(
                    f"✅ Trade placed: {signal.direction} {signal.epic}\n"
                    f"Size: {result.get('size')} | Risk: ${result.get('risk_usd', 0):.2f}\n"
                    f"Type: {result.get('order_type')}",
                    chat_id=str(chat_id),
                )
            else:
                tg.send(
                    f"⚠️ Trade not executed: {result.get('reason', 'Unknown')}",
                    chat_id=str(chat_id),
                )

    # Start the webhook server
    success = webhook.start()
    if success:
        logger.info(f"Webhook receiver ready on port {webhook_config.port}")
    else:
        logger.warning("Webhook receiver failed to start (Flask not installed?)")

    return webhook


def create_bot(demo_override: bool = None):
    """Initialize all bot components."""

    # Determine mode
    use_demo = demo_override if demo_override is not None else USE_DEMO
    base_url = "https://demo-api-capital.backend-capital.com" if use_demo else BASE_URL
    mode_str = "DEMO" if use_demo else "LIVE"

    logger.info(f"{'='*60}")
    logger.info(f"  SWING TRADING BOT - {mode_str} MODE")
    logger.info(f"  Capital.com REST API | Stocks & Crypto CFDs")
    logger.info(f"{'='*60}")

    # Validate credentials
    if not all([CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD]):
        logger.error(
            "Missing credentials! Set environment variables:\n"
            "  CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD\n"
            "Or add them to a .env file."
        )
        sys.exit(1)

    # API Client — lock to specific account if configured
    account_id = CAPITAL_ACCOUNT_ID or None
    api = CapitalComClient(base_url, CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD,
                           account_id=account_id)

    if not api.authenticate():
        logger.error("Authentication failed. Check your credentials.")
        sys.exit(1)

    # List all available accounts so user can identify the right one
    logger.info("Available accounts:")
    try:
        all_accounts = api.list_accounts()
        if account_id:
            logger.info(f">>> LOCKED to account: {account_id}")
        else:
            logger.warning(
                "No CAPITAL_ACCOUNT_ID set! Bot will use the default account.\n"
                "Set CAPITAL_ACCOUNT_ID in .env to lock to your swing trading account.\n"
                "Account IDs are listed above."
            )
    except Exception as e:
        logger.warning(f"Could not list accounts: {e}")

    # Get live account balance
    try:
        balance = api.get_account_balance()
        logger.info(f"Account balance: ${balance:.2f}")
    except Exception:
        balance = 200.0
        logger.warning(f"Could not fetch balance, using default: ${balance:.2f}")

    # Risk Profile
    risk_profile = RiskProfile(initial_capital=balance)
    risk_mgr = RiskManager(
        capital=balance,
        risk_per_trade_pct=risk_profile.risk_per_trade_pct,
        max_daily_risk_pct=risk_profile.max_daily_risk_pct,
        max_positions=risk_profile.max_open_positions,
    )

    logger.info(f"Risk Profile:")
    logger.info(f"  Capital:           ${balance:.2f}")
    logger.info(f"  Max risk/trade:    ${risk_mgr.max_risk_per_trade:.2f} ({risk_profile.risk_per_trade_pct*100}%)")
    logger.info(f"  Max daily risk:    ${risk_mgr.max_daily_risk:.2f} ({risk_profile.max_daily_risk_pct*100}%)")
    logger.info(f"  Max positions:     {risk_profile.max_open_positions}")

    # Position Manager
    pos_mgr = PositionManager(api)

    # Strategy
    strategy_config = SwingStrategyConfig()
    watchlist_config = WatchlistConfig()
    strategy = SwingBreakoutStrategy(api, risk_mgr, pos_mgr, strategy_config, watchlist_config)

    return api, strategy, mode_str, balance


def run_scan(strategy: SwingBreakoutStrategy, tg: TelegramBot = None):
    """One-time scan — find setups and display them."""
    logger.info("Running one-time scan...")
    summary = strategy.run_cycle()

    print("\n" + "="*60)
    print("  SCAN RESULTS")
    print("="*60)

    status = strategy.get_status()

    if status["pending_setups"]:
        for epic, setup in status["pending_setups"].items():
            print(f"\n  {epic} [{setup['pattern']}]")
            print(f"    Score:    {setup['score']}")
            print(f"    Price:    ${setup['current_price']}")
            print(f"    Entry:    ${setup['entry']}")
            print(f"    Stop:     ${setup['stop']}")
            print(f"    Targets:  {setup['targets']}")
            print(f"    R:R:      {setup['risk_reward']}")
            print(f"    Notes:    {setup['notes']}")

        # Telegram notification
        if tg:
            tg.notify_scan_results([s for s in status["pending_setups"].values()])
    else:
        print("\n  No qualifying setups found in this scan.")

    print(f"\n  Risk Status: {json.dumps(status['risk_status'], indent=4)}")
    print("="*60)

    return summary


def run_continuous(strategy: SwingBreakoutStrategy, api: CapitalComClient,
                   tg: TelegramBot = None):
    """
    Main bot loop with Telegram integration.

    Schedule:
    - Check positions every 15 minutes during market hours
    - Full scan at 12 PM, 1 PM, 2 PM, 3 PM ET (midday approach)
    - Daily summary at 9 PM UTC (5 PM ET)
    """
    SCAN_INTERVAL_SECONDS = 900     # 15 minutes
    FULL_SCAN_HOURS_UTC = [16, 17, 18, 19]  # 12-3 PM ET = 16-19 UTC
    SUMMARY_HOUR_UTC = 21           # 5 PM ET = 9 PM UTC
    _summary_sent_today = False

    logger.info("Starting continuous bot loop (Ctrl+C to stop)...")
    logger.info(f"Scan interval: {SCAN_INTERVAL_SECONDS}s | Full scan hours (UTC): {FULL_SCAN_HOURS_UTC}")

    cycle_count = 0

    while _running:
        cycle_count += 1
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        logger.info(f"\n--- Cycle {cycle_count} | {now.strftime('%Y-%m-%d %H:%M UTC')} ---")

        try:
            if current_hour in FULL_SCAN_HOURS_UTC:
                # Full scan + position management
                logger.info("Full scan cycle (midday window)")
                summary = strategy.run_cycle()

                # Telegram: notify on new entries and scan results
                if tg and summary.get("entries_taken", 0) > 0:
                    status = strategy.get_status()
                    if status["pending_setups"]:
                        tg.notify_scan_results(
                            [s for s in status["pending_setups"].values()]
                        )
            else:
                # Position management only
                logger.info("Position management cycle")
                exit_actions = strategy._manage_positions()
                summary = {"note": "management_only", "exits": len(exit_actions)}

                # Telegram: notify on exits
                if tg and exit_actions:
                    for action in exit_actions:
                        tg.notify_trade_exit(
                            epic=action.get("epic", "?"),
                            exit_type=action.get("action", "UNKNOWN"),
                            price=action.get("price", 0),
                            pnl=action.get("pnl", 0),
                            remaining=action.get("remaining", 0),
                        )

            # Daily summary at 5 PM ET
            if current_hour == SUMMARY_HOUR_UTC and not _summary_sent_today:
                if tg:
                    status = strategy.get_status()
                    tg.notify_daily_summary(
                        status["risk_status"],
                        status["open_positions"],
                    )
                _summary_sent_today = True

            # Reset summary flag at midnight UTC
            if current_hour == 0:
                _summary_sent_today = False

            # Log summary
            logger.info(f"Cycle {cycle_count} result: {json.dumps(summary, default=str)}")

        except Exception as e:
            logger.error(f"Cycle {cycle_count} error: {e}", exc_info=True)
            if tg:
                tg.notify_error(f"Cycle {cycle_count}: {str(e)[:200]}")
            # Re-authenticate on connection errors
            try:
                api.authenticate()
            except Exception:
                pass

        # Wait for next cycle
        if _running:
            logger.info(f"Next cycle in {SCAN_INTERVAL_SECONDS}s...")
            for _ in range(SCAN_INTERVAL_SECONDS):
                if not _running:
                    break
                time.sleep(1)

    logger.info("Bot stopped gracefully.")
    if tg:
        tg.send("🛑 <b>Bot stopped.</b> Open positions remain active.")
        tg.stop_polling()


def show_status(strategy: SwingBreakoutStrategy):
    """Display current bot status."""
    status = strategy.get_status()
    print(json.dumps(status, indent=2, default=str))


def main():
    global _telegram

    parser = argparse.ArgumentParser(description="Swing Trading Bot - Capital.com")
    parser.add_argument("--scan", action="store_true", help="Run one-time scan")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--demo", action="store_true", help="Force demo mode")
    args = parser.parse_args()

    # Setup logging
    setup_logging(LOG_DIR, LOG_LEVEL)

    # Load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Create bot
    api, strategy, mode_str, balance = create_bot(
        demo_override=True if args.demo else None
    )

    # Setup Telegram
    tg = setup_telegram(strategy, api)
    _telegram = tg
    if tg:
        tg.start_polling()
        tg.notify_startup(mode_str, balance)
        logger.info("Telegram bot connected and polling")

    # Setup Webhook Receiver
    webhook = setup_webhook(api, risk_mgr, pos_mgr, tg)

    # Run mode
    if args.scan:
        run_scan(strategy, tg)
    elif args.status:
        show_status(strategy)
    else:
        run_continuous(strategy, api, tg)

    # Cleanup
    if tg:
        tg.stop_polling()
    api.logout()


if __name__ == "__main__":
    main()
