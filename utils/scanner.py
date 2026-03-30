"""
Swing Trade Scanner
Scans watchlist for setups matching the 3 chart patterns:
  1. Daily Gap Up (above multi-month resistance)
  2. Long-term Downtrend Break
  3. Oversold Bounce (EMA reclaim after selloff)
"""
import logging
from typing import List, Dict, Optional
from enum import Enum
from dataclasses import dataclass

from .indicators import TechnicalIndicators, CandleData, IndicatorSnapshot

logger = logging.getLogger(__name__)


class PatternType(Enum):
    DAILY_GAP_UP = "daily_gap_up"
    DOWNTREND_BREAK = "downtrend_break"
    OVERSOLD_BOUNCE = "oversold_bounce"


@dataclass
class ScanResult:
    """A detected swing trade setup."""
    epic: str
    pattern: PatternType
    score: float                    # 0-100 confidence score
    current_price: float
    suggested_entry: float
    suggested_stop: float
    suggested_targets: List[float]
    indicators: IndicatorSnapshot
    notes: str = ""

    @property
    def risk_reward_ratio(self) -> float:
        if not self.suggested_targets:
            return 0.0
        reward = abs(self.suggested_targets[0] - self.suggested_entry)
        risk = abs(self.suggested_entry - self.suggested_stop)
        return round(reward / risk, 2) if risk > 0 else 0.0

    def to_dict(self) -> Dict:
        return {
            "epic": self.epic,
            "pattern": self.pattern.value,
            "score": self.score,
            "current_price": self.current_price,
            "entry": self.suggested_entry,
            "stop": self.suggested_stop,
            "targets": self.suggested_targets,
            "risk_reward": self.risk_reward_ratio,
            "above_200sma": self.indicators.is_above_200sma,
            "gap_pct": self.indicators.gap_pct,
            "relative_volume": self.indicators.relative_volume,
            "notes": self.notes,
        }


class SwingScanner:
    """
    Scans instruments for the three swing trading patterns.

    Scanning philosophy (from the strategy):
    - Scan MIDDAY (12 PM - 4 PM ET) for stocks holding gap gains
    - Filter: >$5 price, >$1B market cap, >3% change, >20K volume
    - Then manually (or programmatically) check chart patterns
    """

    def __init__(self, api_client, config):
        self.api = api_client
        self.config = config
        self.indicators = TechnicalIndicators()

    def scan_watchlist(self, epics: List[str]) -> List[ScanResult]:
        """Scan all epics in watchlist and return qualifying setups."""
        results = []

        for epic in epics:
            try:
                result = self._analyze_epic(epic)
                if result and result.score >= 60:  # Minimum confidence threshold
                    results.append(result)
                    logger.info(
                        f"Setup found: {epic} | {result.pattern.value} | "
                        f"Score: {result.score} | R:R {result.risk_reward_ratio}"
                    )
            except Exception as e:
                logger.error(f"Error scanning {epic}: {e}")
                continue

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _analyze_epic(self, epic: str) -> Optional[ScanResult]:
        """Analyze a single instrument for swing trade patterns."""
        # Fetch daily candles (200+ for 200 SMA calculation)
        try:
            price_data = self.api.get_prices(epic, resolution="DAY", max_bars=250)
        except Exception as e:
            logger.debug(f"Could not fetch prices for {epic}: {e}")
            return None

        candles = self._parse_candles(price_data)
        if len(candles) < 200:
            return None

        # Pre-filter: minimum average volume (strategy requires 500K+)
        recent_volumes = [c.volume for c in candles[-20:]]
        avg_recent_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0
        if avg_recent_volume < self.config.min_avg_volume:
            logger.debug(
                f"Skipping {epic}: avg volume {avg_recent_volume:.0f} "
                f"< min {self.config.min_avg_volume}"
            )
            return None

        # Pre-filter: minimum price ($5+ avoids penny stocks)
        current_price = candles[-1].close
        if current_price < 5.0:
            logger.debug(f"Skipping {epic}: price ${current_price:.2f} < $5 minimum")
            return None

        # Run full technical analysis
        snapshot = self.indicators.full_analysis(candles)
        if snapshot is None:
            return None

        # Weekly chart confirmation (score bonus, not a gate)
        weekly_bonus = self._get_weekly_confirmation_bonus(epic)

        # Try each pattern in priority order
        result = self._check_gap_up(epic, candles, snapshot, current_price)
        if result:
            result.score = min(result.score + weekly_bonus, 100)
            if weekly_bonus > 0:
                result.notes += f" | Weekly trend confirmed (+{weekly_bonus}pts)"
            return result

        result = self._check_downtrend_break(epic, candles, snapshot, current_price)
        if result:
            result.score = min(result.score + weekly_bonus, 100)
            if weekly_bonus > 0:
                result.notes += f" | Weekly trend confirmed (+{weekly_bonus}pts)"
            return result

        result = self._check_oversold_bounce(epic, candles, snapshot, current_price)
        if result:
            # Oversold bounce doesn't benefit from weekly uptrend (it's a reversal)
            return result

        return None

    def _check_gap_up(
        self, epic: str, candles: List[CandleData],
        snap: IndicatorSnapshot, price: float
    ) -> Optional[ScanResult]:
        """
        Pattern #1: Daily Gap Up above multi-month resistance.

        Criteria:
        - Gap up >= 3%
        - Gap clears multi-month resistance (at least 2+ months old)
        - Ideally above 200 SMA
        - Volume breakout (relative volume > 2x)
        - Price riding or near 8 EMA
        """
        if snap.gap_pct < self.config.min_gap_pct:
            return None

        # Must be holding gap gains midday (current price above today's open)
        # This filters out stocks that gapped up but are fading intraday
        today_open = candles[-1].open
        if price < today_open:
            return None

        # Check if gap is above resistance
        if not snap.resistance_levels:
            return None

        # Gap must clear the nearest resistance
        nearest_resistance = snap.resistance_levels[-1]
        if price < nearest_resistance:
            return None

        # Score the setup
        score = 50  # Base score for meeting gap criteria

        # Bonus: price well above open = strong holding pattern
        holding_strength = (price - today_open) / today_open * 100
        if holding_strength > 1.0:
            score += 5

        if snap.is_above_200sma:
            score += 15
        if snap.is_volume_breakout:
            score += 15
        if snap.is_above_8ema:
            score += 10
        if snap.gap_pct > 5:
            score += 5
        if snap.relative_volume > 3:
            score += 5

        # Entry: near 8 EMA on pullback
        entry = snap.ema_8
        # Stop: below the breakout resistance level
        stop = nearest_resistance * (1 - self.config.stop_buffer_pct / 100)
        # Targets: based on extensions
        targets = self._calculate_targets(entry, stop, snap)

        return ScanResult(
            epic=epic,
            pattern=PatternType.DAILY_GAP_UP,
            score=min(score, 100),
            current_price=price,
            suggested_entry=round(entry, 2),
            suggested_stop=round(stop, 2),
            suggested_targets=targets,
            indicators=snap,
            notes=f"Gap: {snap.gap_pct}% above resistance {nearest_resistance}",
        )

    def _check_downtrend_break(
        self, epic: str, candles: List[CandleData],
        snap: IndicatorSnapshot, price: float
    ) -> Optional[ScanResult]:
        """
        Pattern #2: Long-term downtrend break.

        Criteria:
        - Breaking above a downtrend line (connecting swing highs over 2+ months)
        - Reclaiming 200 SMA
        - High volume on breakout
        - Price above 8 EMA
        """
        trend_line = self.indicators.detect_downtrend_line(candles, lookback=90)
        if trend_line is None:
            return None

        slope, intercept = trend_line
        # Current trendline value at the latest bar
        trendline_price = slope * (len(candles) - 1) + intercept

        # Price must be above the downtrend line
        if price < trendline_price:
            return None

        score = 50

        if snap.is_above_200sma:
            score += 20  # Critical for this pattern
        if snap.is_volume_breakout:
            score += 15
        if snap.is_above_8ema:
            score += 10
        if price > trendline_price * 1.02:  # 2% above trendline = confirmation
            score += 5

        entry = max(snap.sma_200, snap.ema_8)  # Enter above 200 SMA
        stop = snap.sma_200 * 0.97  # Stop below 200 SMA with buffer

        if snap.support_levels:
            stop = min(stop, snap.support_levels[-1] * 0.985)

        targets = self._calculate_targets(entry, stop, snap)

        return ScanResult(
            epic=epic,
            pattern=PatternType.DOWNTREND_BREAK,
            score=min(score, 100),
            current_price=price,
            suggested_entry=round(entry, 2),
            suggested_stop=round(stop, 2),
            suggested_targets=targets,
            indicators=snap,
            notes=f"Downtrend break confirmed. Trendline was at {trendline_price:.2f}",
        )

    def _check_oversold_bounce(
        self, epic: str, candles: List[CandleData],
        snap: IndicatorSnapshot, price: float
    ) -> Optional[ScanResult]:
        """
        Pattern #3: Oversold bounce.

        Criteria:
        - Stock dropped below 200 SMA in recent weeks
        - Significant selloff (10%+ from recent high)
        - Price now reclaiming 8 EMA after being below it for days
        - NOT trying to catch the falling knife — wait for EMA reclaim
        """
        if snap.is_above_200sma:
            return None  # Must be below 200 SMA for this pattern

        # Check for significant recent drop
        recent_high = max(c.high for c in candles[-30:])
        drop_pct = (recent_high - price) / recent_high * 100
        if drop_pct < 10:
            return None  # Not oversold enough

        # Must be reclaiming 8 EMA (current close above EMA, previous below)
        closes = [c.close for c in candles]
        ema_values = self.indicators.ema(closes, 8)
        if len(ema_values) < 3:
            return None

        # EMA reclaim: today above, yesterday below
        if not (closes[-1] > ema_values[-1] and closes[-2] < ema_values[-2]):
            return None

        score = 50

        if drop_pct > 20:
            score += 10
        if snap.is_volume_breakout:
            score += 10
        if snap.is_above_8ema:
            score += 15
        # Closer to 200 SMA = more upside room
        distance_to_200 = abs(snap.sma_200 - price) / price * 100
        if distance_to_200 > 5:
            score += 10

        entry = snap.ema_8  # Enter at 8 EMA reclaim
        # Stop: low of the reclaim candle
        stop = candles[-1].low * 0.985

        targets = [
            round(snap.sma_200, 2),  # Target 1: back to 200 SMA
        ]
        if snap.resistance_levels:
            targets.extend([round(r, 2) for r in snap.resistance_levels[:2]])

        return ScanResult(
            epic=epic,
            pattern=PatternType.OVERSOLD_BOUNCE,
            score=min(score, 100),
            current_price=price,
            suggested_entry=round(entry, 2),
            suggested_stop=round(stop, 2),
            suggested_targets=targets,
            indicators=snap,
            notes=f"Oversold bounce: {drop_pct:.1f}% drop, EMA reclaim in progress",
        )

    def _get_weekly_confirmation_bonus(self, epic: str) -> int:
        """
        Check the weekly chart for trend confirmation.
        Strategy: weekly 8 EMA trending up + price above weekly 200 SMA = strong context.
        Returns bonus score points (0-10).
        """
        try:
            weekly_data = self.api.get_prices(epic, resolution="WEEK", max_bars=220)
            weekly_candles = self._parse_candles(weekly_data)
            if len(weekly_candles) < 200:
                return 0

            closes = [c.close for c in weekly_candles]
            weekly_ema_8 = self.indicators.ema(closes, 8)
            weekly_sma_200 = self.indicators.sma(closes, 200)

            if not weekly_ema_8 or not weekly_sma_200:
                return 0

            bonus = 0
            current = closes[-1]

            # Price above weekly 200 SMA = long-term uptrend intact
            if current > weekly_sma_200[-1] and weekly_sma_200[-1] > 0:
                bonus += 5

            # Weekly 8 EMA rising (current > 3 weeks ago) = momentum
            if len(weekly_ema_8) > 3 and weekly_ema_8[-1] > weekly_ema_8[-4]:
                bonus += 5

            return bonus

        except Exception as e:
            logger.debug(f"Weekly confirmation unavailable for {epic}: {e}")
            return 0

    def _calculate_targets(
        self, entry: float, stop: float, snap: IndicatorSnapshot
    ) -> List[float]:
        """
        Calculate profit targets based on risk multiples and resistance levels.
        Minimum 2:1 R:R for first target, then resistance-based.
        """
        risk = abs(entry - stop)
        targets = []

        # Target 1: 2:1 R:R
        targets.append(round(entry + (risk * 2), 2))

        # Target 2: nearest resistance or 3:1 R:R
        if snap.resistance_levels:
            for r in snap.resistance_levels:
                if r > entry + risk:
                    targets.append(round(r, 2))
                    break
        if len(targets) < 2:
            targets.append(round(entry + (risk * 3), 2))

        # Target 3: 200 SMA or 4:1 R:R
        if snap.sma_200 > entry + risk * 2:
            targets.append(round(snap.sma_200, 2))
        else:
            targets.append(round(entry + (risk * 4), 2))

        return targets[:4]  # Max 4 targets for quarter exits

    def _parse_candles(self, price_data: Dict) -> List[CandleData]:
        """Parse Capital.com price response into CandleData objects."""
        candles = []
        prices = price_data.get("prices", [])

        for p in prices:
            try:
                # Capital.com returns bid/ask OHLC — use mid prices
                snap_time = p.get("snapshotTime", "")
                open_bid = p.get("openPrice", {}).get("bid", 0)
                open_ask = p.get("openPrice", {}).get("ask", 0)
                high_bid = p.get("highPrice", {}).get("bid", 0)
                high_ask = p.get("highPrice", {}).get("ask", 0)
                low_bid = p.get("lowPrice", {}).get("bid", 0)
                low_ask = p.get("lowPrice", {}).get("ask", 0)
                close_bid = p.get("closePrice", {}).get("bid", 0)
                close_ask = p.get("closePrice", {}).get("ask", 0)
                volume = p.get("lastTradedVolume", 0)

                candles.append(CandleData(
                    timestamp=snap_time,
                    open=(open_bid + open_ask) / 2,
                    high=(high_bid + high_ask) / 2,
                    low=(low_bid + low_ask) / 2,
                    close=(close_bid + close_ask) / 2,
                    volume=int(volume),
                ))
            except (KeyError, TypeError, ValueError) as e:
                logger.debug(f"Skipping malformed candle: {e}")
                continue

        return candles
