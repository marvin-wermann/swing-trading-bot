"""
Swing Trading Bot - Configuration Settings
Capital.com REST API | Stocks & Crypto CFDs
Risk Profile: 2% per trade, 5% per day max
"""
import os
from dataclasses import dataclass, field
from typing import List

# ──────────────────────────────────────────────
# ENVIRONMENT: Set these in .env or export them
# ──────────────────────────────────────────────
CAPITAL_API_KEY = os.getenv("CAPITAL_API_KEY", "")
CAPITAL_EMAIL = os.getenv("CAPITAL_EMAIL", "")
CAPITAL_PASSWORD = os.getenv("CAPITAL_PASSWORD", "")
CAPITAL_ACCOUNT_ID = os.getenv("CAPITAL_ACCOUNT_ID", "")  # Lock to specific sub-account

# API Base URLs
DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com"
LIVE_BASE_URL = "https://api-capital.backend-capital.com"

# Toggle: start on DEMO, switch to LIVE when confident
USE_DEMO = os.getenv("USE_DEMO", "true").lower() == "true"
BASE_URL = DEMO_BASE_URL if USE_DEMO else LIVE_BASE_URL
API_VERSION = "api/v1"


@dataclass
class RiskProfile:
    """
    Risk management parameters.
    With $200 capital and 2% risk per trade = $4 max risk per position.
    5% daily cap = $10 max daily drawdown.
    """
    initial_capital: float = 200.0          # Starting capital in USD
    risk_per_trade_pct: float = 0.02        # 2% risk per trade
    max_daily_risk_pct: float = 0.05        # 5% max daily risk
    max_open_positions: int = 3             # Quality over quantity
    max_risk_per_trade_usd: float = field(init=False)
    max_daily_risk_usd: float = field(init=False)

    def __post_init__(self):
        self.max_risk_per_trade_usd = self.initial_capital * self.risk_per_trade_pct
        self.max_daily_risk_usd = self.initial_capital * self.max_daily_risk_pct

    def update_capital(self, new_capital: float):
        """Recalculate risk limits when capital changes."""
        self.initial_capital = new_capital
        self.max_risk_per_trade_usd = new_capital * self.risk_per_trade_pct
        self.max_daily_risk_usd = new_capital * self.max_daily_risk_pct


@dataclass
class SwingStrategyConfig:
    """
    Swing trading strategy parameters derived from the
    breakout-pullback-to-8EMA methodology.
    """
    # Indicators
    ema_fast_period: int = 8                # 8 EMA - momentum / entry trigger
    sma_slow_period: int = 200              # 200 SMA - long-term trend filter
    volume_avg_period: int = 20             # Average volume lookback

    # Timeframes
    primary_timeframe: str = "DAY"          # Daily chart for analysis
    secondary_timeframe: str = "WEEK"       # Weekly chart for confirmation
    entry_timeframe: str = "HOUR_4"         # 4H for entry precision

    # Chart Pattern Filters
    min_gap_pct: float = 3.0               # Minimum 3% overnight gap
    min_market_cap: float = 1_000_000_000  # $1B+ market cap only
    min_avg_volume: int = 500_000           # Minimum average daily volume
    min_relative_volume: float = 2.0        # Relative volume > 2x average

    # Entry Rules
    require_above_200sma: bool = True       # Stock must be above 200 SMA
    require_ema_reclaim: bool = True         # Price must reclaim 8 EMA
    pullback_to_ema_tolerance_pct: float = 1.5  # Entry within 1.5% of 8 EMA

    # Exit Rules - Partial Profit Taking (quarters)
    partial_exit_portions: int = 4          # Exit in 4 tranches (25% each)
    first_target_extension_pct: float = 5.0 # First target: 5% extension from EMA
    trailing_stop_atr_multiplier: float = 2.0  # Trail stop at 2x ATR

    # Stop Loss
    stop_below_support: bool = True         # Stop below swing low / support
    stop_buffer_pct: float = 1.5            # 1.5% buffer below support level

    # Scan window (midday approach)
    scan_start_hour_utc: int = 16           # 12 PM ET = 16 UTC
    scan_end_hour_utc: int = 20             # 4 PM ET = 20 UTC


@dataclass
class WebhookConfig:
    """
    Webhook receiver configuration.
    Accepts signals from TradingView, Telegram /trade, or any HTTP POST.
    All signals go through the same risk manager — no bypassing.
    """
    enabled: bool = os.getenv("WEBHOOK_ENABLED", "true").lower() == "true"
    port: int = int(os.getenv("WEBHOOK_PORT", "5000"))
    host: str = "0.0.0.0"
    secret_token: str = os.getenv("WEBHOOK_SECRET", "")


@dataclass
class DayTradingConfig:
    """Day trading config scaffold - to be expanded."""
    initial_capital: float = 100.0
    risk_per_trade_pct: float = 0.02
    max_daily_risk_pct: float = 0.05
    timeframe: str = "MINUTE_5"
    ema_period: int = 9
    vwap_enabled: bool = True


@dataclass
class ScalpingConfig:
    """Scalping bot integration config scaffold."""
    existing_bot_path: str = ""             # Path to your current scalping bot
    reuse_signals: bool = True              # Re-use scalper signals for swing confirmation
    bridge_mode: str = "webhook"            # webhook | file | redis


@dataclass
class WatchlistConfig:
    """Instruments to monitor."""
    # Stock CFDs - large cap with high liquidity
    stock_epics: List[str] = field(default_factory=lambda: [
        # Tech giants
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        # Tech / growth
        "AMD", "CRM", "NFLX", "INTC", "ORCL", "ADBE", "SHOP",
        "SQ", "PLTR", "SNAP", "UBER", "COIN", "PYPL",
        # Finance
        "JPM", "V", "MA", "GS", "BAC", "WFC", "C",
        # Consumer / retail
        "NKE", "SBUX", "HD", "DG", "LULU", "MCD", "WMT", "COST", "TGT",
        # Industrial / energy
        "BA", "DIS", "GM", "F", "XOM", "CVX", "CAT",
        # Healthcare / biotech
        "JNJ", "PFE", "UNH", "MRNA", "ABBV",
        # Semiconductors
        "AVGO", "QCOM", "MU", "MRVL", "ARM",
        # In the news / high momentum (March 2026)
        "TLYS", "SOC", "BW", "AMPX", "CURV",   # March top gainers
        "SMCI", "RIVN", "LCID", "SOFI", "RBLX", # Volatile movers
        "CRWD", "PANW", "ZS",                    # Cybersecurity (hot sector)
    ])
    # Crypto CFDs
    crypto_epics: List[str] = field(default_factory=lambda: [
        "BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "XRPUSD",
        "ADAUSD", "DOTUSD", "AVAXUSD", "LINKUSD", "MATICUSD",
        "DOGEUSD", "SHIBUSD", "NEARUSD", "APTUSD", "SUIUSD",
    ])

    @property
    def all_epics(self) -> List[str]:
        return self.stock_epics + self.crypto_epics


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
TRADE_LOG_FILE = os.path.join(LOG_DIR, "trades.json")
SIGNAL_LOG_FILE = os.path.join(LOG_DIR, "signals.json")
