"""
Webhook Receiver
Listens for incoming trade signals from any external source:
  - TradingView free tier (email→webhook bridge)
  - Manual Telegram /trade commands
  - Other Python scripts / bots
  - Discord bots, API calls, etc.

All signals go through the same risk manager and position manager
as the built-in scanner — no trade bypasses risk rules.
"""
import os
import json
import logging
import hashlib
import hmac
from typing import Optional, Dict, List, Callable
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from threading import Thread

logger = logging.getLogger(__name__)


class SignalSource(Enum):
    TRADINGVIEW = "tradingview"
    TELEGRAM = "telegram"
    MANUAL = "manual"
    EXTERNAL = "external"


@dataclass
class TradeSignal:
    """Normalized trade signal from any source."""
    epic: str
    direction: str          # BUY or SELL
    entry_price: float      # 0 = market order at current price
    stop_price: float
    targets: List[float]    # profit targets
    source: SignalSource
    source_name: str = ""   # e.g. "TradingView RSI Divergence"
    timeframe: str = ""     # e.g. "1H", "4H", "D"
    notes: str = ""
    raw_payload: Dict = None

    def validate(self) -> tuple:
        """Basic validation. Returns (is_valid, error_message)."""
        if not self.epic:
            return False, "Missing epic/symbol"
        if self.direction not in ("BUY", "SELL"):
            return False, f"Invalid direction: {self.direction}"
        if self.stop_price <= 0:
            return False, "Missing or invalid stop price"
        if self.direction == "BUY" and self.entry_price > 0 and self.stop_price >= self.entry_price:
            return False, f"BUY stop ({self.stop_price}) must be below entry ({self.entry_price})"
        if self.direction == "SELL" and self.entry_price > 0 and self.stop_price <= self.entry_price:
            return False, f"SELL stop ({self.stop_price}) must be above entry ({self.entry_price})"
        return True, ""

    def to_dict(self) -> Dict:
        return {
            "epic": self.epic,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "targets": self.targets,
            "source": self.source.value,
            "source_name": self.source_name,
            "timeframe": self.timeframe,
            "notes": self.notes,
        }


class WebhookReceiver:
    """
    HTTP webhook endpoint that accepts trade signals.
    Runs as a lightweight Flask server on a configurable port.

    Security: validates requests via HMAC token or simple secret header.

    Signal flow:
      HTTP POST → parse → validate → risk check → execute
    """

    def __init__(self, secret_token: str, port: int = 5000, host: str = "0.0.0.0"):
        self.secret_token = secret_token
        self.port = port
        self.host = host
        self._app = None
        self._server_thread: Optional[Thread] = None
        self._signal_callback: Optional[Callable] = None
        self._signal_log_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs", "webhook_signals.json"
        )

    def on_signal(self, callback: Callable):
        """Register callback for when a valid signal is received.
        Callback signature: callback(signal: TradeSignal) -> Dict (result)
        """
        self._signal_callback = callback

    def start(self):
        """Start the webhook server in a background thread."""
        try:
            from flask import Flask, request, jsonify
        except ImportError:
            logger.error(
                "Flask not installed. Run: pip install flask\n"
                "Webhook receiver disabled."
            )
            return False

        app = Flask(__name__)
        app.logger.setLevel(logging.WARNING)  # Quiet Flask logs
        self._app = app

        @app.route("/health", methods=["GET"])
        def health():
            return jsonify({"status": "ok", "service": "swing-trading-webhook"})

        @app.route("/webhook/tradingview", methods=["POST"])
        def tradingview_webhook():
            """
            TradingView alert webhook endpoint.

            Expected JSON body (set this in TradingView alert message):
            {
                "secret": "YOUR_WEBHOOK_SECRET",
                "epic": "AAPL",
                "direction": "BUY",
                "entry": 0,
                "stop": 185.50,
                "target1": 200.00,
                "target2": 210.00,
                "strategy": "RSI Divergence",
                "timeframe": "4H",
                "notes": "Bullish RSI divergence on 4H"
            }

            Or simplified TradingView format:
            {
                "secret": "YOUR_WEBHOOK_SECRET",
                "ticker": "AAPL",
                "action": "buy",
                "price": 192.50,
                "stop": 185.50,
                "take_profit": 200.00
            }
            """
            return self._handle_signal(request, SignalSource.TRADINGVIEW)

        @app.route("/webhook/signal", methods=["POST"])
        def generic_webhook():
            """Generic webhook for any signal source."""
            return self._handle_signal(request, SignalSource.EXTERNAL)

        # Start in background thread
        self._server_thread = Thread(
            target=lambda: app.run(
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
            ),
            daemon=True,
        )
        self._server_thread.start()
        logger.info(f"Webhook receiver started on {self.host}:{self.port}")
        logger.info(f"  TradingView endpoint: http://YOUR_VPS_IP:{self.port}/webhook/tradingview")
        logger.info(f"  Generic endpoint:     http://YOUR_VPS_IP:{self.port}/webhook/signal")
        return True

    def _handle_signal(self, request, source: SignalSource):
        """Process an incoming webhook request."""
        from flask import jsonify

        # Parse JSON body
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Invalid JSON body"}), 400

        if not data:
            return jsonify({"error": "Empty request body"}), 400

        # Authenticate — check secret token
        req_secret = data.get("secret", "")
        # Also check header-based auth
        header_secret = request.headers.get("X-Webhook-Secret", "")

        if not self._verify_secret(req_secret or header_secret):
            logger.warning(f"Webhook auth failed from {request.remote_addr}")
            return jsonify({"error": "Invalid secret"}), 401

        # Parse into normalized signal
        try:
            signal = self._parse_signal(data, source)
        except Exception as e:
            logger.error(f"Failed to parse webhook signal: {e}")
            return jsonify({"error": f"Parse error: {str(e)}"}), 400

        # Validate
        is_valid, error = signal.validate()
        if not is_valid:
            return jsonify({"error": f"Invalid signal: {error}"}), 400

        # Log the signal
        self._log_signal(signal, data)

        # Execute via callback
        if self._signal_callback:
            try:
                result = self._signal_callback(signal)
                return jsonify({
                    "status": "executed",
                    "signal": signal.to_dict(),
                    "result": result,
                }), 200
            except Exception as e:
                logger.error(f"Signal execution failed: {e}", exc_info=True)
                return jsonify({
                    "status": "error",
                    "signal": signal.to_dict(),
                    "error": str(e),
                }), 500
        else:
            return jsonify({
                "status": "received",
                "signal": signal.to_dict(),
                "warning": "No execution callback registered",
            }), 200

    def _verify_secret(self, provided: str) -> bool:
        """Constant-time secret comparison to prevent timing attacks."""
        if not self.secret_token:
            return True  # No auth configured (not recommended)
        return hmac.compare_digest(provided, self.secret_token)

    def _parse_signal(self, data: Dict, source: SignalSource) -> TradeSignal:
        """
        Parse various webhook formats into a normalized TradeSignal.
        Supports TradingView format, simplified format, and generic format.
        """
        # Normalize epic/symbol
        epic = (
            data.get("epic") or
            data.get("ticker") or
            data.get("symbol") or
            data.get("instrument", "")
        ).upper().strip()

        # Normalize direction
        direction_raw = (
            data.get("direction") or
            data.get("action") or
            data.get("side", "")
        ).upper().strip()

        direction = "BUY" if direction_raw in ("BUY", "LONG") else "SELL"

        # Prices
        entry = float(data.get("entry") or data.get("price") or data.get("entry_price") or 0)
        stop = float(data.get("stop") or data.get("stop_price") or data.get("stop_loss") or 0)

        # Targets — support multiple formats
        targets = []
        for key in ["target1", "target2", "target3", "target4"]:
            if key in data and data[key]:
                targets.append(float(data[key]))
        if not targets:
            tp = data.get("take_profit") or data.get("tp") or data.get("target")
            if tp:
                targets.append(float(tp))
        if not targets and "targets" in data:
            targets = [float(t) for t in data["targets"]]

        # Metadata
        source_name = data.get("strategy") or data.get("source_name") or data.get("indicator", "")
        timeframe = data.get("timeframe") or data.get("interval") or data.get("tf", "")
        notes = data.get("notes") or data.get("comment") or data.get("message", "")

        return TradeSignal(
            epic=epic,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            targets=targets,
            source=source,
            source_name=source_name,
            timeframe=timeframe,
            notes=notes,
            raw_payload=data,
        )

    def _log_signal(self, signal: TradeSignal, raw_data: Dict):
        """Log received signal to file for audit trail."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal": signal.to_dict(),
            "raw": {k: v for k, v in raw_data.items() if k != "secret"},
        }
        try:
            os.makedirs(os.path.dirname(self._signal_log_path), exist_ok=True)
            log = []
            if os.path.exists(self._signal_log_path):
                with open(self._signal_log_path, "r") as f:
                    log = json.load(f)
            log.append(entry)
            # Keep last 500 signals
            log = log[-500:]
            with open(self._signal_log_path, "w") as f:
                json.dump(log, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to log webhook signal: {e}")


class TelegramSignalParser:
    """
    Parses /trade commands from Telegram into TradeSignals.

    Formats supported:
      /trade AAPL buy 192.50 stop 185.50 tp 200
      /trade BTC sell stop 68000 tp 65000
      /trade NVDA long 880 stop 850 tp 920 tp 950
    """

    @staticmethod
    def parse(text: str) -> Optional[TradeSignal]:
        """Parse a /trade command into a TradeSignal."""
        parts = text.strip().split()

        if len(parts) < 4:
            return None

        # Remove /trade prefix if present
        if parts[0].lower() in ("/trade", "trade"):
            parts = parts[1:]

        epic = parts[0].upper()

        # Parse direction
        direction_raw = parts[1].upper()
        if direction_raw in ("BUY", "LONG", "B", "L"):
            direction = "BUY"
        elif direction_raw in ("SELL", "SHORT", "S"):
            direction = "SELL"
        else:
            return None

        # Parse remaining key-value pairs
        entry = 0.0
        stop = 0.0
        targets = []

        i = 2
        while i < len(parts):
            token = parts[i].lower()

            if token in ("stop", "sl", "stoploss"):
                if i + 1 < len(parts):
                    try:
                        stop = float(parts[i + 1])
                        i += 2
                        continue
                    except ValueError:
                        pass
            elif token in ("tp", "target", "take_profit", "profit"):
                if i + 1 < len(parts):
                    try:
                        targets.append(float(parts[i + 1]))
                        i += 2
                        continue
                    except ValueError:
                        pass
            else:
                # Might be the entry price (a bare number after direction)
                try:
                    val = float(token)
                    if entry == 0:
                        entry = val
                    i += 1
                    continue
                except ValueError:
                    pass
            i += 1

        if stop <= 0:
            return None

        return TradeSignal(
            epic=epic,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            targets=targets,
            source=SignalSource.TELEGRAM,
            source_name="Manual /trade command",
        )
