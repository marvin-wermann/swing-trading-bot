"""
Technical Indicators for Swing Trading
Implements: 8 EMA, 200 SMA, Volume analysis, ATR, Support/Resistance detection.
"""
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CandleData:
    """Single OHLCV candle."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class IndicatorSnapshot:
    """All indicator values at a point in time."""
    ema_8: float
    sma_200: float
    avg_volume: float
    relative_volume: float
    atr_14: float
    is_above_200sma: bool
    is_above_8ema: bool
    is_riding_ema: bool          # Price within 1.5% of 8 EMA
    is_volume_breakout: bool     # Volume > 2x average
    gap_pct: float               # Overnight gap percentage
    support_levels: List[float]
    resistance_levels: List[float]


class TechnicalIndicators:
    """
    Pure-Python technical indicator calculations.
    No pandas/numpy dependency — keeps the bot lightweight for VPS deployment.
    """

    @staticmethod
    def ema(closes: List[float], period: int) -> List[float]:
        """Exponential Moving Average."""
        if len(closes) < period:
            return []
        multiplier = 2 / (period + 1)
        ema_values = [sum(closes[:period]) / period]  # SMA seed

        for price in closes[period:]:
            ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])

        # Pad with None-equivalents for alignment
        return [0.0] * (period - 1) + ema_values

    @staticmethod
    def sma(values: List[float], period: int) -> List[float]:
        """Simple Moving Average."""
        if len(values) < period:
            return []
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(0.0)
            else:
                window = values[i - period + 1: i + 1]
                result.append(sum(window) / period)
        return result

    @staticmethod
    def atr(candles: List[CandleData], period: int = 14) -> List[float]:
        """Average True Range — measures volatility for stop placement."""
        if len(candles) < 2:
            return []

        true_ranges = []
        for i in range(1, len(candles)):
            high_low = candles[i].high - candles[i].low
            high_prev_close = abs(candles[i].high - candles[i - 1].close)
            low_prev_close = abs(candles[i].low - candles[i - 1].close)
            true_ranges.append(max(high_low, high_prev_close, low_prev_close))

        # ATR as SMA of True Ranges
        atr_values = []
        for i in range(len(true_ranges)):
            if i < period - 1:
                atr_values.append(0.0)
            else:
                window = true_ranges[i - period + 1: i + 1]
                atr_values.append(sum(window) / period)

        return [0.0] + atr_values  # Pad first element

    @staticmethod
    def volume_analysis(
        volumes: List[int], period: int = 20
    ) -> Tuple[List[float], List[float]]:
        """
        Returns (avg_volumes, relative_volumes).
        Relative volume = current / average — above 2.0 signals institutional interest.
        """
        avg = TechnicalIndicators.sma([float(v) for v in volumes], period)
        relative = []
        for i, vol in enumerate(volumes):
            if avg[i] > 0:
                relative.append(vol / avg[i])
            else:
                relative.append(0.0)
        return avg, relative

    @staticmethod
    def detect_gap(candles: List[CandleData]) -> float:
        """
        Calculate overnight gap percentage.
        Gap% = (Today Open - Yesterday Close) / Yesterday Close * 100
        """
        if len(candles) < 2:
            return 0.0
        prev_close = candles[-2].close
        curr_open = candles[-1].open
        if prev_close == 0:
            return 0.0
        return ((curr_open - prev_close) / prev_close) * 100

    @staticmethod
    def find_support_resistance(
        candles: List[CandleData], lookback: int = 120, tolerance_pct: float = 1.0
    ) -> Tuple[List[float], List[float]]:
        """
        Detect key support and resistance levels from swing highs/lows.
        These are the multi-month levels the strategy requires for gap-above entries.
        Uses 120-day lookback (approx 6 months) to ensure levels are truly multi-month.
        """
        if len(candles) < 5:
            return [], []

        swing_highs = []
        swing_lows = []

        # Use last `lookback` candles
        data = candles[-lookback:] if len(candles) > lookback else candles

        for i in range(2, len(data) - 2):
            # Swing high: higher than 2 candles on each side
            if (data[i].high > data[i - 1].high and data[i].high > data[i - 2].high and
                data[i].high > data[i + 1].high and data[i].high > data[i + 2].high):
                swing_highs.append(data[i].high)

            # Swing low: lower than 2 candles on each side
            if (data[i].low < data[i - 1].low and data[i].low < data[i - 2].low and
                data[i].low < data[i + 1].low and data[i].low < data[i + 2].low):
                swing_lows.append(data[i].low)

        # Cluster nearby levels (within tolerance_pct)
        resistance = TechnicalIndicators._cluster_levels(swing_highs, tolerance_pct)
        support = TechnicalIndicators._cluster_levels(swing_lows, tolerance_pct)

        return support, resistance

    @staticmethod
    def _cluster_levels(levels: List[float], tolerance_pct: float) -> List[float]:
        """Group nearby price levels into clusters (stronger levels)."""
        if not levels:
            return []

        sorted_levels = sorted(levels)
        clusters = []
        current_cluster = [sorted_levels[0]]

        for i in range(1, len(sorted_levels)):
            pct_diff = abs(sorted_levels[i] - current_cluster[-1]) / current_cluster[-1] * 100
            if pct_diff <= tolerance_pct:
                current_cluster.append(sorted_levels[i])
            else:
                clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [sorted_levels[i]]

        clusters.append(sum(current_cluster) / len(current_cluster))
        return [round(c, 2) for c in clusters]

    @staticmethod
    def detect_downtrend_line(candles: List[CandleData], lookback: int = 90) -> Optional[Tuple[float, float]]:
        """
        Detect long-term downtrend line by connecting swing highs.
        Returns (slope, intercept) or None if no clear downtrend.
        Used for Chart Pattern #2: Long-term downtrend break.
        """
        data = candles[-lookback:] if len(candles) > lookback else candles

        # Find swing highs
        swing_high_points = []
        for i in range(2, len(data) - 2):
            if (data[i].high > data[i - 1].high and data[i].high > data[i - 2].high and
                data[i].high > data[i + 1].high and data[i].high > data[i + 2].high):
                swing_high_points.append((i, data[i].high))

        if len(swing_high_points) < 2:
            return None

        # Simple linear regression on swing highs
        n = len(swing_high_points)
        sum_x = sum(p[0] for p in swing_high_points)
        sum_y = sum(p[1] for p in swing_high_points)
        sum_xy = sum(p[0] * p[1] for p in swing_high_points)
        sum_x2 = sum(p[0] ** 2 for p in swing_high_points)

        denom = n * sum_x2 - sum_x ** 2
        if denom == 0:
            return None

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # Only return if slope is negative (downtrend)
        if slope < 0:
            return (slope, intercept)
        return None

    @classmethod
    def full_analysis(cls, candles: List[CandleData]) -> Optional[IndicatorSnapshot]:
        """
        Run complete indicator suite on candle data.
        This is the main entry point called by the scanner and strategy.
        """
        if len(candles) < 200:
            logger.warning(f"Need at least 200 candles, got {len(candles)}")
            return None

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        ema_8_values = cls.ema(closes, 8)
        sma_200_values = cls.sma(closes, 200)
        atr_values = cls.atr(candles, 14)
        avg_vols, rel_vols = cls.volume_analysis(volumes, 20)
        gap_pct = cls.detect_gap(candles)
        support, resistance = cls.find_support_resistance(candles)

        current_price = closes[-1]
        ema_8 = ema_8_values[-1] if ema_8_values else 0
        sma_200 = sma_200_values[-1] if sma_200_values else 0

        # Is price "riding" the 8 EMA? (within 1.5%)
        ema_distance_pct = abs(current_price - ema_8) / ema_8 * 100 if ema_8 > 0 else 999

        return IndicatorSnapshot(
            ema_8=round(ema_8, 2),
            sma_200=round(sma_200, 2),
            avg_volume=round(avg_vols[-1], 0) if avg_vols else 0,
            relative_volume=round(rel_vols[-1], 2) if rel_vols else 0,
            atr_14=round(atr_values[-1], 2) if atr_values else 0,
            is_above_200sma=current_price > sma_200,
            is_above_8ema=current_price > ema_8,
            is_riding_ema=ema_distance_pct <= 1.5,
            is_volume_breakout=rel_vols[-1] > 2.0 if rel_vols else False,
            gap_pct=round(gap_pct, 2),
            support_levels=support[-3:],       # Last 3 support levels
            resistance_levels=resistance[-3:], # Last 3 resistance levels
        )
