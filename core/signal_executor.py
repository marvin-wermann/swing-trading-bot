"""
Signal Executor
Bridges external signals (webhooks, Telegram /trade, manual) into the
existing risk manager → position manager → Capital.com execution pipeline.

Every signal goes through the same rules:
  - 2% risk per trade
  - 5% daily max drawdown
  - Max 3 open positions
  - Position sizing based on entry-to-stop distance
  - Quarter-system exit management
"""
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

from core.api_client import CapitalComClient
from core.risk_manager import RiskManager, TradeRisk
from core.position_manager import PositionManager, ManagedPosition
from core.webhook_receiver import TradeSignal

logger = logging.getLogger(__name__)


class SignalExecutor:
    """
    Takes a TradeSignal from any source and executes it through
    the same pipeline as the scanner-generated trades.
    """

    def __init__(self, api: CapitalComClient, risk_mgr: RiskManager,
                 pos_mgr: PositionManager, telegram=None):
        self.api = api
        self.risk = risk_mgr
        self.positions = pos_mgr
        self.telegram = telegram

    def execute(self, signal: TradeSignal) -> Dict:
        """
        Execute a trade signal. Returns result dict.

        Flow:
        1. Validate signal format
        2. Get current market price if entry is 0 (market order)
        3. Run through risk manager
        4. Place order via Capital.com API
        5. Register with position manager
        6. Notify via Telegram
        """
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal": signal.to_dict(),
            "executed": False,
            "reason": "",
        }

        # Step 1: Basic validation
        is_valid, error = signal.validate()
        if not is_valid:
            result["reason"] = f"Invalid signal: {error}"
            logger.warning(result["reason"])
            return result

        # Step 2: Resolve entry price
        entry_price = signal.entry_price
        if entry_price <= 0:
            # Market order — fetch current price
            try:
                price_data = self.api.get_prices(signal.epic, resolution="HOUR", max_bars=1)
                prices = price_data.get("prices", [])
                if prices:
                    bid = prices[-1].get("closePrice", {}).get("bid", 0)
                    ask = prices[-1].get("closePrice", {}).get("ask", 0)
                    entry_price = (bid + ask) / 2
                else:
                    result["reason"] = f"Could not fetch current price for {signal.epic}"
                    logger.error(result["reason"])
                    return result
            except Exception as e:
                result["reason"] = f"Price fetch failed for {signal.epic}: {e}"
                logger.error(result["reason"])
                return result

        # Step 3: Risk validation
        first_target = signal.targets[0] if signal.targets else None
        trade_risk = self.risk.validate_trade(
            epic=signal.epic,
            direction=signal.direction,
            entry_price=entry_price,
            stop_price=signal.stop_price,
            target_price=first_target,
        )

        if trade_risk is None:
            result["reason"] = "Rejected by risk manager"
            logger.info(f"Webhook signal rejected: {signal.epic} — risk manager blocked")
            if self.telegram:
                self.telegram.send(
                    f"⚠️ <b>SIGNAL REJECTED</b>\n"
                    f"Source: {signal.source.value} ({signal.source_name})\n"
                    f"Epic: {signal.epic} {signal.direction}\n"
                    f"Reason: Risk manager blocked (check limits)"
                )
            return result

        # Step 4: Place order
        try:
            # Check if we should use market or limit order
            if signal.entry_price <= 0 or abs(entry_price - signal.entry_price) / entry_price < 0.02:
                # Market order — price is close enough or no specific entry requested
                order_result = self.api.create_position(
                    epic=signal.epic,
                    direction=signal.direction,
                    size=trade_risk.size,
                    stop_level=signal.stop_price,
                    profit_level=first_target,
                )
                order_type = "MARKET"
            else:
                # Limit order — price hasn't reached entry yet
                order_result = self.api.create_working_order(
                    epic=signal.epic,
                    direction=signal.direction,
                    size=trade_risk.size,
                    level=signal.entry_price,
                    order_type="LIMIT",
                    stop_level=signal.stop_price,
                    profit_level=first_target,
                )
                order_type = "LIMIT"

            deal_id = order_result.get("dealReference",
                                       order_result.get("dealId", "unknown"))

        except Exception as e:
            result["reason"] = f"Order failed: {e}"
            logger.error(f"Webhook order execution failed for {signal.epic}: {e}")
            if self.telegram:
                self.telegram.send(
                    f"❌ <b>ORDER FAILED</b>\n"
                    f"Source: {signal.source.value}\n"
                    f"Epic: {signal.epic} {signal.direction}\n"
                    f"Error: {str(e)[:200]}"
                )
            return result

        # Step 5: Register with position manager
        managed = ManagedPosition(
            epic=signal.epic,
            deal_id=deal_id,
            direction=signal.direction,
            entry_price=entry_price,
            initial_size=trade_risk.size,
            remaining_size=trade_risk.size,
            stop_price=signal.stop_price,
            target_prices=signal.targets or [entry_price * 1.06],  # Default 6% target if none
        )
        self.positions.add_position(managed)
        self.risk.register_trade(trade_risk)

        # Step 6: Notify
        logger.info(
            f"WEBHOOK ENTRY: {signal.direction} {trade_risk.size} {signal.epic} @ {entry_price} | "
            f"Stop: {signal.stop_price} | Source: {signal.source.value} ({signal.source_name})"
        )

        if self.telegram:
            targets_str = " → ".join([f"${t:.2f}" for t in signal.targets]) if signal.targets else "N/A"
            self.telegram.send(
                f"🔔 <b>WEBHOOK TRADE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📡 Source: {signal.source.value} ({signal.source_name})\n"
                f"📈 <b>{signal.direction} {signal.epic}</b>\n"
                f"📋 Order: {order_type}\n"
                f"💰 Size: {trade_risk.size} units\n"
                f"🎯 Entry: <b>${entry_price:.2f}</b>\n"
                f"🛑 Stop: ${signal.stop_price:.2f}\n"
                f"🏁 Targets: {targets_str}\n"
                f"⚖️ Risk: ${trade_risk.risk_usd:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )

        result["executed"] = True
        result["order_type"] = order_type
        result["deal_id"] = deal_id
        result["size"] = trade_risk.size
        result["risk_usd"] = round(trade_risk.risk_usd, 2)
        return result
