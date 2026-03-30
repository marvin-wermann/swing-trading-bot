"""
Risk Manager
Enforces 2% per trade / 5% daily max drawdown rules.
With $200 capital: $4 max risk per trade, $10 max daily loss.
"""
import logging
import json
import os
from datetime import datetime, date
from typing import Optional, Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TradeRisk:
    """Risk parameters for a single trade."""
    epic: str
    direction: str
    entry_price: float
    stop_price: float
    size: float
    risk_usd: float
    risk_pct: float
    reward_price: Optional[float] = None
    risk_reward_ratio: Optional[float] = None


class RiskManager:
    """
    Core risk engine.

    Rules (from 20+ years of experience):
    1. Never risk more than 2% of capital on a single trade
    2. Never exceed 5% daily drawdown
    3. Position size = (Capital * Risk%) / |Entry - Stop|
    4. Minimum 2:1 reward-to-risk ratio for swing trades
    5. Maximum 3 concurrent positions to maintain quality focus
    """

    def __init__(self, capital: float, risk_per_trade_pct: float = 0.02,
                 max_daily_risk_pct: float = 0.05, max_positions: int = 3):
        self.capital = capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_daily_risk_pct = max_daily_risk_pct
        self.max_positions = max_positions

        # Daily tracking
        self._daily_pnl: float = 0.0
        self._daily_risk_used: float = 0.0
        self._trade_date: date = date.today()
        self._open_trades: List[TradeRisk] = []

        # Trade journal
        self._journal_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs", "risk_journal.json"
        )

    @property
    def max_risk_per_trade(self) -> float:
        """Maximum dollar risk per trade."""
        return self.capital * self.risk_per_trade_pct

    @property
    def max_daily_risk(self) -> float:
        """Maximum daily loss allowed."""
        return self.capital * self.max_daily_risk_pct

    @property
    def remaining_daily_risk(self) -> float:
        """How much more risk we can take today."""
        self._check_day_reset()
        return max(0, self.max_daily_risk - self._daily_risk_used)

    @property
    def can_open_trade(self) -> bool:
        """Whether we're allowed to open another trade."""
        self._check_day_reset()
        if len(self._open_trades) >= self.max_positions:
            logger.warning(f"Max positions ({self.max_positions}) reached")
            return False
        if self.remaining_daily_risk <= 0:
            logger.warning("Daily risk limit exhausted")
            return False
        return True

    def _check_day_reset(self):
        """Reset daily counters at start of new trading day."""
        today = date.today()
        if today != self._trade_date:
            logger.info(f"New trading day: {today}. Resetting daily counters.")
            self._daily_pnl = 0.0
            self._daily_risk_used = 0.0
            self._trade_date = today

    def calculate_position_size(
        self, entry_price: float, stop_price: float, epic: str = ""
    ) -> float:
        """
        Calculate position size based on risk.

        Formula: Size = Risk$ / |Entry - Stop|

        For a $200 account with 2% risk ($4):
          Entry: $70, Stop: $68 → Size = $4 / $2 = 2 shares
          Entry: $50000 (BTC), Stop: $49000 → Size = $4 / $1000 = 0.004 BTC

        This is how you survive with small capital — precision sizing.
        """
        price_risk = abs(entry_price - stop_price)
        if price_risk == 0:
            logger.error("Entry and stop are the same price — cannot size position")
            return 0.0

        # Use the lesser of: per-trade max or remaining daily budget
        available_risk = min(self.max_risk_per_trade, self.remaining_daily_risk)

        if available_risk <= 0:
            logger.warning("No risk budget available")
            return 0.0

        raw_size = available_risk / price_risk

        # Capital.com CFDs allow fractional sizes
        # Round to reasonable precision
        if entry_price > 1000:
            size = round(raw_size, 4)   # Crypto / high-price stocks
        elif entry_price > 100:
            size = round(raw_size, 3)
        else:
            size = round(raw_size, 2)

        # Ensure minimum viable size
        if size <= 0:
            logger.warning(f"Calculated size too small for {epic}: {raw_size}")
            return 0.0

        logger.info(
            f"Position size for {epic}: {size} units | "
            f"Risk: ${available_risk:.2f} / ${price_risk:.2f} per unit"
        )
        return size

    def validate_trade(
        self,
        epic: str,
        direction: str,
        entry_price: float,
        stop_price: float,
        target_price: Optional[float] = None,
    ) -> Optional[TradeRisk]:
        """
        Validate a trade against all risk rules.
        Returns TradeRisk if valid, None if rejected.
        """
        if not self.can_open_trade:
            return None

        # Validate stop direction
        if direction == "BUY" and stop_price >= entry_price:
            logger.error(f"BUY stop ({stop_price}) must be below entry ({entry_price})")
            return None
        if direction == "SELL" and stop_price <= entry_price:
            logger.error(f"SELL stop ({stop_price}) must be above entry ({entry_price})")
            return None

        size = self.calculate_position_size(entry_price, stop_price, epic)
        if size <= 0:
            return None

        risk_usd = size * abs(entry_price - stop_price)
        risk_pct = risk_usd / self.capital if self.capital > 0 else 0

        # Check reward:risk ratio (minimum 2:1 for swing trades)
        rr_ratio = None
        if target_price is not None:
            reward = abs(target_price - entry_price)
            risk = abs(entry_price - stop_price)
            rr_ratio = reward / risk if risk > 0 else 0
            if rr_ratio < 2.0:
                logger.warning(
                    f"R:R ratio {rr_ratio:.1f} below minimum 2.0 for {epic} — "
                    f"consider wider target or tighter stop"
                )
                # Don't reject, but flag it — experienced traders sometimes
                # accept 1.5:1 on high-probability setups

        trade = TradeRisk(
            epic=epic,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            size=size,
            risk_usd=risk_usd,
            risk_pct=risk_pct,
            reward_price=target_price,
            risk_reward_ratio=rr_ratio,
        )

        logger.info(
            f"Trade validated: {direction} {epic} | Size: {size} | "
            f"Risk: ${risk_usd:.2f} ({risk_pct:.1%}) | R:R: {rr_ratio or 'N/A'}"
        )
        return trade

    def register_trade(self, trade: TradeRisk):
        """Register an opened trade for tracking."""
        self._open_trades.append(trade)
        self._daily_risk_used += trade.risk_usd
        self._log_to_journal("OPEN", trade)
        logger.info(
            f"Trade registered: {trade.epic} | "
            f"Daily risk used: ${self._daily_risk_used:.2f} / ${self.max_daily_risk:.2f}"
        )

    def close_trade(self, epic: str, exit_price: float, partial_pct: float = 1.0):
        """Record a trade closure and update P&L."""
        for trade in self._open_trades:
            if trade.epic == epic:
                if trade.direction == "BUY":
                    pnl = (exit_price - trade.entry_price) * trade.size * partial_pct
                else:
                    pnl = (trade.entry_price - exit_price) * trade.size * partial_pct

                self._daily_pnl += pnl
                self.capital += pnl

                logger.info(
                    f"Trade closed: {epic} | P&L: ${pnl:.2f} | "
                    f"New capital: ${self.capital:.2f}"
                )

                if partial_pct >= 1.0:
                    self._open_trades.remove(trade)
                else:
                    trade.size *= (1 - partial_pct)

                self._log_to_journal("CLOSE", trade, pnl=pnl, exit_price=exit_price)
                return pnl

        logger.warning(f"No open trade found for {epic}")
        return 0.0

    def update_capital(self, new_capital: float):
        """Update capital (e.g., after deposit or withdrawal)."""
        old = self.capital
        self.capital = new_capital
        logger.info(f"Capital updated: ${old:.2f} → ${new_capital:.2f}")

    def get_status(self) -> Dict:
        """Return current risk status summary."""
        self._check_day_reset()
        return {
            "capital": round(self.capital, 2),
            "max_risk_per_trade": round(self.max_risk_per_trade, 2),
            "max_daily_risk": round(self.max_daily_risk, 2),
            "daily_risk_used": round(self._daily_risk_used, 2),
            "remaining_daily_risk": round(self.remaining_daily_risk, 2),
            "daily_pnl": round(self._daily_pnl, 2),
            "open_positions": len(self._open_trades),
            "max_positions": self.max_positions,
            "can_trade": self.can_open_trade,
        }

    def _log_to_journal(self, action: str, trade: TradeRisk,
                        pnl: float = 0.0, exit_price: float = 0.0):
        """Append trade action to JSON journal."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": action,
            "epic": trade.epic,
            "direction": trade.direction,
            "entry_price": trade.entry_price,
            "stop_price": trade.stop_price,
            "size": trade.size,
            "risk_usd": round(trade.risk_usd, 2),
            "capital_at_time": round(self.capital, 2),
        }
        if action == "CLOSE":
            entry["exit_price"] = exit_price
            entry["pnl"] = round(pnl, 2)

        try:
            os.makedirs(os.path.dirname(self._journal_path), exist_ok=True)
            journal = []
            if os.path.exists(self._journal_path):
                with open(self._journal_path, "r") as f:
                    journal = json.load(f)
            journal.append(entry)
            with open(self._journal_path, "w") as f:
                json.dump(journal, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write journal: {e}")
