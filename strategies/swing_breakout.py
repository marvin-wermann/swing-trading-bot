"""
Swing Breakout Strategy
The primary strategy: Daily gap up / breakout → pullback to 8 EMA → ride the trend.

This is the exact methodology from the video content, codified:
  1. Midday scan for stocks gapping up 3%+ and holding gains
  2. Filter through chart pattern criteria (gap above multi-month resistance)
  3. Wait for pullback to 8 EMA for entry
  4. Stop below support / breakout level
  5. Take profits in quarters at progressive targets
  6. Trail final portion with 8 EMA trailing stop
"""
import logging
from typing import Optional, Dict, List
from datetime import datetime, timezone

from core.api_client import CapitalComClient
from core.risk_manager import RiskManager, TradeRisk
from core.position_manager import PositionManager, ManagedPosition
from utils.scanner import SwingScanner, ScanResult, PatternType
from utils.indicators import TechnicalIndicators, CandleData
from config.settings import SwingStrategyConfig, WatchlistConfig

logger = logging.getLogger(__name__)


class SwingBreakoutStrategy:
    """
    Orchestrates the full swing trading workflow.

    Lifecycle:
      scan() → evaluate() → enter() → manage() → exit()

    Called by the main bot loop on schedule.
    """

    def __init__(
        self,
        api: CapitalComClient,
        risk_mgr: RiskManager,
        pos_mgr: PositionManager,
        strategy_config: SwingStrategyConfig = None,
        watchlist_config: WatchlistConfig = None,
    ):
        self.api = api
        self.risk = risk_mgr
        self.positions = pos_mgr
        self.config = strategy_config or SwingStrategyConfig()
        self.watchlist = watchlist_config or WatchlistConfig()
        self.scanner = SwingScanner(api, self.config)
        self.indicators = TechnicalIndicators()

        # Track pending setups (found by scanner, waiting for entry)
        self._pending_setups: Dict[str, ScanResult] = {}

    def run_cycle(self) -> Dict:
        """
        Execute one full strategy cycle. Called by the main loop.
        Returns summary of actions taken.
        """
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanned": 0,
            "setups_found": 0,
            "entries_taken": 0,
            "exits_taken": 0,
            "errors": [],
        }

        try:
            # Phase 1: Manage existing positions (check exits first — capital preservation)
            exit_actions = self._manage_positions()
            summary["exits_taken"] = len(exit_actions)

            # Phase 2: Scan for new setups (if we have capacity)
            if self.risk.can_open_trade:
                setups = self._scan_for_setups()
                summary["scanned"] = len(self.watchlist.all_epics)
                summary["setups_found"] = len(setups)

                # Phase 3: Evaluate and enter best setups
                for setup in setups:
                    if not self.risk.can_open_trade:
                        break
                    entered = self._try_enter(setup)
                    if entered:
                        summary["entries_taken"] += 1

        except Exception as e:
            logger.error(f"Strategy cycle error: {e}", exc_info=True)
            summary["errors"].append(str(e))

        logger.info(
            f"Cycle complete: {summary['setups_found']} setups, "
            f"{summary['entries_taken']} entries, {summary['exits_taken']} exits"
        )
        return summary

    def _scan_for_setups(self) -> List[ScanResult]:
        """Run the scanner on the full watchlist."""
        logger.info(f"Scanning {len(self.watchlist.all_epics)} instruments...")
        setups = self.scanner.scan_watchlist(self.watchlist.all_epics)

        # Add to pending and log
        for setup in setups:
            self._pending_setups[setup.epic] = setup
            logger.info(
                f"New setup: {setup.epic} | {setup.pattern.value} | "
                f"Score: {setup.score} | Entry: {setup.suggested_entry} | "
                f"Stop: {setup.suggested_stop} | R:R: {setup.risk_reward_ratio}"
            )

        return setups

    def _try_enter(self, setup: ScanResult) -> bool:
        """
        Attempt to enter a swing trade based on a scan result.

        Entry logic:
        - For gap-ups: enter if price is within 1.5% of 8 EMA (pullback entry)
        - For downtrend breaks: enter above 200 SMA
        - For oversold bounces: enter on 8 EMA reclaim

        In all cases, we prefer LIMIT orders near the EMA rather than market orders.
        """
        # Validate against risk management
        trade_risk = self.risk.validate_trade(
            epic=setup.epic,
            direction="BUY",  # All three patterns are bullish swing setups
            entry_price=setup.suggested_entry,
            stop_price=setup.suggested_stop,
            target_price=setup.suggested_targets[0] if setup.suggested_targets else None,
        )

        if trade_risk is None:
            logger.info(f"Trade rejected by risk manager: {setup.epic}")
            return False

        # Check if price is at a good entry point right now
        current_price = setup.current_price
        entry_zone_pct = abs(current_price - setup.suggested_entry) / setup.suggested_entry * 100

        if entry_zone_pct > 2.0:
            # Price is too far from ideal entry — place a limit order instead
            logger.info(
                f"{setup.epic}: Price ({current_price}) is {entry_zone_pct:.1f}% from "
                f"ideal entry ({setup.suggested_entry}). Placing limit order."
            )
            return self._place_limit_entry(setup, trade_risk)

        # Market entry — price is in the zone
        return self._place_market_entry(setup, trade_risk)

    def _place_market_entry(self, setup: ScanResult, trade_risk: TradeRisk) -> bool:
        """Execute a market order entry."""
        try:
            result = self.api.create_position(
                epic=setup.epic,
                direction="BUY",
                size=trade_risk.size,
                stop_level=setup.suggested_stop,
                profit_level=setup.suggested_targets[0] if setup.suggested_targets else None,
            )

            deal_id = result.get("dealReference", result.get("dealId", "unknown"))

            # Register with position manager
            managed = ManagedPosition(
                epic=setup.epic,
                deal_id=deal_id,
                direction="BUY",
                entry_price=setup.current_price,
                initial_size=trade_risk.size,
                remaining_size=trade_risk.size,
                stop_price=setup.suggested_stop,
                target_prices=setup.suggested_targets,
            )
            self.positions.add_position(managed)
            self.risk.register_trade(trade_risk)

            logger.info(
                f"ENTRY: BUY {trade_risk.size} {setup.epic} @ {setup.current_price} | "
                f"Stop: {setup.suggested_stop} | Targets: {setup.suggested_targets}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to enter {setup.epic}: {e}")
            return False

    def _place_limit_entry(self, setup: ScanResult, trade_risk: TradeRisk) -> bool:
        """
        Place a limit order at the ideal entry price.
        Falls back to market order if limit order API fails.
        """
        try:
            result = self.api.create_working_order(
                epic=setup.epic,
                direction="BUY",
                size=trade_risk.size,
                level=setup.suggested_entry,
                order_type="LIMIT",
                stop_level=setup.suggested_stop,
                profit_level=setup.suggested_targets[0] if setup.suggested_targets else None,
                good_till_date=None,  # Will auto-set to 30 days
            )

            logger.info(
                f"LIMIT ORDER: BUY {trade_risk.size} {setup.epic} @ {setup.suggested_entry} | "
                f"Stop: {setup.suggested_stop}"
            )
            return True

        except Exception as e:
            logger.warning(
                f"Limit order failed for {setup.epic}: {e} — "
                f"Falling back to market order at current price"
            )
            # Fallback: use market order instead of letting the trade slip away
            return self._place_market_entry(setup, trade_risk)

    def _manage_positions(self) -> List[Dict]:
        """Check all open positions for exit conditions."""
        actions = []
        open_positions = self.positions.get_open_positions()

        for pos_data in open_positions:
            epic = pos_data["epic"]
            try:
                # Fetch current price and 8 EMA
                price_data = self.api.get_prices(epic, resolution="DAY", max_bars=20)
                candles = self.scanner._parse_candles(price_data)
                if not candles:
                    continue

                current_price = candles[-1].close
                closes = [c.close for c in candles]
                ema_values = self.indicators.ema(closes, 8)
                ema_8 = ema_values[-1] if ema_values else current_price

                # Check exit conditions
                exit_actions = self.positions.check_exits(epic, current_price, ema_8)
                actions.extend(exit_actions)

                # Update risk manager on any exits
                for action in exit_actions:
                    if action.get("pnl") is not None:
                        self.risk.close_trade(
                            epic,
                            action["price"],
                            partial_pct=action.get("size", 0) / pos_data.get("initial_size", 1)
                        )

            except Exception as e:
                logger.error(f"Error managing position {epic}: {e}")

        return actions

    def get_status(self) -> Dict:
        """Return current strategy status."""
        return {
            "risk_status": self.risk.get_status(),
            "open_positions": self.positions.get_open_positions(),
            "pending_setups": {
                k: v.to_dict() for k, v in self._pending_setups.items()
            },
        }
