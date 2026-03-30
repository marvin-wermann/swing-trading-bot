"""
Day Trading Bot - Scaffold
To be expanded with dedicated $100 capital allocation.

Strategy: 5-minute chart breakouts with VWAP confirmation.
Same risk profile: 2% per trade ($2), 5% daily max ($5).

This scaffold shares the same core infrastructure:
  - CapitalComClient (same broker, separate account recommended)
  - RiskManager (independent instance with $100 capital)
  - PositionManager (separate state file)

Key differences from swing trading:
  - MINUTE_5 timeframe instead of DAY
  - 9 EMA instead of 8 EMA
  - VWAP as primary trend filter (instead of 200 SMA)
  - Intraday entries and exits (no overnight holds)
  - Tighter stops (ATR-based on 5-min chart)
  - Pre-market scanning (4:00-6:30 AM ET for gappers)
"""
import logging
from typing import Dict, Optional

from core.api_client import CapitalComClient
from core.risk_manager import RiskManager
from core.position_manager import PositionManager
from config.settings import DayTradingConfig

logger = logging.getLogger(__name__)


class DayTradingStrategy:
    """
    Day trading strategy scaffold.
    Expand this class to implement intraday breakout logic.
    """

    def __init__(
        self,
        api: CapitalComClient,
        config: DayTradingConfig = None,
    ):
        self.api = api
        self.config = config or DayTradingConfig()

        # Independent risk manager for day trading capital
        self.risk = RiskManager(
            capital=self.config.initial_capital,
            risk_per_trade_pct=self.config.risk_per_trade_pct,
            max_daily_risk_pct=self.config.max_daily_risk_pct,
            max_positions=5,  # More positions allowed for day trading
        )

        # Separate state file for day trading positions
        self.positions = PositionManager(
            api,
            state_file="data/day_trading_positions.json"
        )

    def run_cycle(self) -> Dict:
        """
        Placeholder for day trading cycle.

        Implementation plan:
        1. Pre-market scan (4:00 AM ET): Find gappers using same scanner
           but with MINUTE_5 resolution and tighter filters
        2. Opening bell (9:30 AM ET): Monitor for breakout confirmation
        3. First 30 min: Execute entries on confirmed breakouts
        4. Midday: Manage positions, trail stops
        5. Power hour (3:00-4:00 PM ET): Close all remaining positions
        6. NO overnight holds

        Indicators needed:
        - 9 EMA on 5-min chart
        - VWAP (Volume Weighted Average Price)
        - Level 2 / order flow (future enhancement)
        - Pre-market high/low as key levels
        """
        logger.info("Day trading scaffold — not yet implemented")
        return {"status": "scaffold", "message": "Day trading strategy pending implementation"}

    def premarket_scan(self) -> Dict:
        """
        Scan for gappers in pre-market.
        Filters: >3% gap, >$5 price, >20K pre-market volume
        """
        # TODO: Implement pre-market scanning using Capital.com API
        # The API supports extended hours data for some instruments
        pass

    def calculate_vwap(self, candles) -> float:
        """
        Calculate VWAP for intraday trend filter.
        VWAP = Cumulative(Price * Volume) / Cumulative(Volume)
        """
        # TODO: Implement VWAP calculation
        pass
