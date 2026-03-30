---
name: swing-trading-bot
description: Swing trading strategy bot for Capital.com using breakout-pullback-to-8EMA methodology on stocks and crypto CFDs
triggers:
  - swing trade
  - swing trading
  - breakout strategy
  - capital.com bot
  - trading bot
  - 8 EMA pullback
version: "1.0.0"
---

# Swing Trading Bot Skill

## Strategy Overview

This bot implements a technical breakout swing trading strategy focused on:

1. **Daily Gap Up** (Pattern #1): Stocks gapping up 3%+ above multi-month resistance
2. **Long-term Downtrend Break** (Pattern #2): Breaking above downtrend lines while reclaiming 200 SMA
3. **Oversold Bounce** (Pattern #3): EMA reclaim after significant selloff below 200 SMA

## Indicators

- **8 EMA**: Momentum indicator for entries (pullback-to-EMA entries)
- **200 SMA**: Long-term trend filter (above = bullish, below = avoid)
- **Volume**: Confirmation of breakouts (relative volume > 2x average)
- **ATR**: Volatility measure for stop placement

## Risk Management

- 2% risk per trade (with $200 capital = $4 max risk)
- 5% daily max drawdown ($10)
- Maximum 3 concurrent positions
- Minimum 2:1 reward-to-risk ratio
- Position sizing: Risk$ / |Entry - Stop|

## Exit Strategy (Quarter System)

1. Exit 25% at Target 1 → move stop to breakeven
2. Exit 25% at Target 2 → activate trailing stop
3. Exit 25% at Target 3
4. Trail remaining 25% with 8 EMA trailing stop

## Workflow

```
Midday Scan (12-4 PM ET)
  → Filter: >$5, >$1B cap, >3% gap, >20K volume
  → Chart Pattern Match (gap-up / downtrend-break / oversold-bounce)
  → Indicator Confirmation (above 200 SMA, volume breakout, EMA alignment)
  → Risk Validation (2% max, R:R > 2:1)
  → Entry: Pullback to 8 EMA or limit order at EMA
  → Management: Partial exits + trailing stop
```

## Configuration

All parameters are in `config/settings.py`. Key adjustables:
- `min_gap_pct`: Minimum gap percentage (default: 3%)
- `pullback_to_ema_tolerance_pct`: Entry zone around EMA (default: 1.5%)
- `partial_exit_portions`: Number of exit tranches (default: 4)
- `trailing_stop_atr_multiplier`: Trailing stop distance (default: 2x ATR)

## Integration with tradermonty/claude-trading-skills

This bot's SKILL.md follows the same format as the claude-trading-skills repo.
Compatible skills from that repo that complement this bot:
- `finviz-screener`: Pre-filter stocks before scanning
- `technical-analysis`: Additional chart pattern validation
- `backtesting`: Validate strategy on historical data
- `market-breadth`: Confirm market conditions before taking trades
