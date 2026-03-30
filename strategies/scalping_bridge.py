"""
Scalping Bot Bridge
Integrates with your existing custom scalping bot running on Ubuntu VPS.

Three integration modes:
  1. Webhook: Scalper sends HTTP signals to this bot
  2. File:    Scalper writes signals to a shared JSON file
  3. Redis:   Scalper publishes to Redis pub/sub channel

The bridge receives scalping signals and can:
  a) Use them as confirmation for swing trade entries
  b) Relay them to Capital.com for execution
  c) Aggregate short-term momentum into swing trade bias
"""
import os
import json
import time
import logging
from typing import Dict, Optional, List, Callable
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScalpSignal:
    """Signal from the existing scalping bot."""
    epic: str
    direction: str          # BUY or SELL
    price: float
    confidence: float       # 0-1
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source: str = "scalper"
    metadata: Dict = field(default_factory=dict)


class ScalpingBridge:
    """
    Bridge between your existing scalping bot and this swing trading system.

    Usage:
      bridge = ScalpingBridge(mode="file", signal_path="/path/to/scalper/signals.json")
      signals = bridge.read_signals()
      bias = bridge.aggregate_bias("BTCUSD")  # Returns bullish/bearish/neutral
    """

    def __init__(
        self,
        mode: str = "file",
        signal_path: str = None,
        webhook_port: int = 8080,
        redis_channel: str = "scalper_signals",
    ):
        self.mode = mode
        self.signal_path = signal_path or "/tmp/scalper_signals.json"
        self.webhook_port = webhook_port
        self.redis_channel = redis_channel
        self._signal_buffer: List[ScalpSignal] = []
        self._callbacks: List[Callable] = []

    def read_signals(self) -> List[ScalpSignal]:
        """Read latest signals from the scalping bot."""
        if self.mode == "file":
            return self._read_file_signals()
        elif self.mode == "webhook":
            return self._get_webhook_signals()
        elif self.mode == "redis":
            return self._read_redis_signals()
        return []

    def aggregate_bias(self, epic: str, lookback_minutes: int = 60) -> str:
        """
        Aggregate recent scalping signals into a directional bias.
        This is used to confirm swing trade entries.

        Returns: 'bullish', 'bearish', or 'neutral'
        """
        signals = [s for s in self._signal_buffer if s.epic == epic]

        if not signals:
            return "neutral"

        buy_signals = sum(1 for s in signals if s.direction == "BUY")
        sell_signals = sum(1 for s in signals if s.direction == "SELL")
        total = buy_signals + sell_signals

        if total == 0:
            return "neutral"

        buy_ratio = buy_signals / total

        if buy_ratio > 0.65:
            return "bullish"
        elif buy_ratio < 0.35:
            return "bearish"
        return "neutral"

    def on_signal(self, callback: Callable):
        """Register a callback for new signals."""
        self._callbacks.append(callback)

    def _read_file_signals(self) -> List[ScalpSignal]:
        """Read signals from shared JSON file."""
        if not os.path.exists(self.signal_path):
            return []

        try:
            with open(self.signal_path, "r") as f:
                data = json.load(f)

            signals = []
            for entry in data:
                sig = ScalpSignal(
                    epic=entry.get("epic", ""),
                    direction=entry.get("direction", ""),
                    price=entry.get("price", 0),
                    confidence=entry.get("confidence", 0.5),
                    timestamp=entry.get("timestamp", ""),
                    metadata=entry.get("metadata", {}),
                )
                signals.append(sig)
                self._signal_buffer.append(sig)

            # Keep buffer manageable
            self._signal_buffer = self._signal_buffer[-500:]

            return signals
        except Exception as e:
            logger.error(f"Error reading scalper signals: {e}")
            return []

    def _get_webhook_signals(self) -> List[ScalpSignal]:
        """
        Webhook mode — requires running a small HTTP server.

        To integrate your scalping bot:
        1. Have your bot POST signals to http://localhost:8080/signal
        2. JSON body: {"epic": "BTCUSD", "direction": "BUY", "price": 50000, "confidence": 0.8}

        TODO: Implement with FastAPI or Flask for production use.
        """
        logger.info("Webhook mode — server needs to be started separately")
        return []

    def _read_redis_signals(self) -> List[ScalpSignal]:
        """
        Redis pub/sub mode.

        To integrate:
        1. pip install redis
        2. Your scalper publishes: redis.publish('scalper_signals', json.dumps(signal))
        3. This bridge subscribes and processes

        TODO: Implement Redis subscriber.
        """
        logger.info("Redis mode — requires redis-py package")
        return []

    def write_signal_for_scalper(self, signal: Dict, output_path: str = None):
        """
        Write a signal back to the scalper (bidirectional communication).
        For example, when swing trade finds a high-conviction setup,
        tell the scalper to increase size on that instrument.
        """
        path = output_path or self.signal_path.replace(".json", "_swing_hints.json")
        try:
            existing = []
            if os.path.exists(path):
                with open(path, "r") as f:
                    existing = json.load(f)

            existing.append({
                **signal,
                "source": "swing_bot",
                "timestamp": datetime.utcnow().isoformat(),
            })

            # Keep last 100 signals
            existing = existing[-100:]

            with open(path, "w") as f:
                json.dump(existing, f, indent=2)

        except Exception as e:
            logger.error(f"Error writing signal for scalper: {e}")
