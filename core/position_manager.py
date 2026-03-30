"""
Position Manager
Handles partial exits, trailing stops, and position lifecycle for swing trades.
"""
import logging
import json
import os
from typing import Optional, Dict, List
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ManagedPosition:
    """A swing trade position with full lifecycle management."""
    epic: str
    deal_id: str
    direction: str              # BUY or SELL
    entry_price: float
    initial_size: float
    remaining_size: float
    stop_price: float
    target_prices: List[float]  # Multiple targets for partial exits
    current_target_idx: int = 0
    trailing_stop: Optional[float] = None
    opened_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    partial_exits: List[Dict] = field(default_factory=list)
    status: str = "OPEN"       # OPEN, PARTIAL, CLOSED

    @property
    def unrealized_pnl(self) -> float:
        """Placeholder — updated with live price externally."""
        return 0.0

    def to_dict(self) -> Dict:
        return {
            "epic": self.epic,
            "deal_id": self.deal_id,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "initial_size": self.initial_size,
            "remaining_size": self.remaining_size,
            "stop_price": self.stop_price,
            "target_prices": self.target_prices,
            "current_target_idx": self.current_target_idx,
            "trailing_stop": self.trailing_stop,
            "opened_at": self.opened_at,
            "partial_exits": self.partial_exits,
            "status": self.status,
        }


class PositionManager:
    """
    Manages open swing trade positions.

    Exit Strategy (Quarter System):
      - Exit 25% at Target 1 (first resistance / 5% extension from EMA)
      - Exit 25% at Target 2 (next resistance level)
      - Exit 25% at Target 3 (major resistance / measured move)
      - Trail remaining 25% with EMA-based trailing stop

    After first partial exit, move stop to breakeven.
    After second partial, trail the stop at 2x ATR below price.
    """

    def __init__(self, api_client, state_file: str = None):
        self.api = api_client
        self.positions: Dict[str, ManagedPosition] = {}
        self._state_file = state_file or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "positions.json"
        )
        self._load_state()

    def add_position(self, position: ManagedPosition):
        """Register a new managed position."""
        self.positions[position.epic] = position
        self._save_state()
        logger.info(f"Position added: {position.epic} @ {position.entry_price}")

    def check_exits(self, epic: str, current_price: float, ema_8_value: float) -> List[Dict]:
        """
        Check if any exit conditions are met for a position.
        Returns list of actions taken.
        """
        if epic not in self.positions:
            return []

        pos = self.positions[epic]
        if pos.status == "CLOSED":
            return []

        actions = []
        portion_size = pos.initial_size / 4  # Quarter system

        # Check stop loss hit
        if self._is_stopped_out(pos, current_price):
            actions.append(self._execute_full_exit(pos, current_price, "STOP_LOSS"))
            return actions

        # Check trailing stop
        if pos.trailing_stop and self._is_stopped_out_trailing(pos, current_price):
            actions.append(self._execute_full_exit(pos, current_price, "TRAILING_STOP"))
            return actions

        # Check partial profit targets
        if pos.current_target_idx < len(pos.target_prices):
            target = pos.target_prices[pos.current_target_idx]

            if self._target_hit(pos, current_price, target):
                action = self._execute_partial_exit(
                    pos, current_price, portion_size, pos.current_target_idx
                )
                actions.append(action)

                # After first partial: move stop to breakeven
                if pos.current_target_idx == 1:
                    pos.stop_price = pos.entry_price
                    self._update_stop_on_broker(pos)
                    logger.info(f"{epic}: Stop moved to breakeven @ {pos.entry_price}")

                # After second partial: activate trailing stop
                if pos.current_target_idx == 2:
                    self._activate_trailing_stop(pos, current_price, ema_8_value)

        # Update trailing stop if active
        elif pos.trailing_stop:
            self._update_trailing_stop(pos, current_price, ema_8_value)

        self._save_state()
        return actions

    def _is_stopped_out(self, pos: ManagedPosition, price: float) -> bool:
        """Check if price has hit the stop loss."""
        if pos.direction == "BUY":
            return price <= pos.stop_price
        return price >= pos.stop_price

    def _is_stopped_out_trailing(self, pos: ManagedPosition, price: float) -> bool:
        """Check if price has hit the trailing stop."""
        if pos.trailing_stop is None:
            return False
        if pos.direction == "BUY":
            return price <= pos.trailing_stop
        return price >= pos.trailing_stop

    def _target_hit(self, pos: ManagedPosition, price: float, target: float) -> bool:
        """Check if price has reached a profit target."""
        if pos.direction == "BUY":
            return price >= target
        return price <= target

    def _execute_partial_exit(
        self, pos: ManagedPosition, price: float, size: float, target_idx: int
    ) -> Dict:
        """Execute a partial position exit."""
        actual_size = min(size, pos.remaining_size)

        # In live trading, we'd reduce the position via API
        # Capital.com requires closing and re-opening at reduced size,
        # or using the update endpoint if supported
        logger.info(
            f"PARTIAL EXIT: {pos.epic} | Target {target_idx + 1} hit @ {price} | "
            f"Closing {actual_size} of {pos.remaining_size}"
        )

        pos.remaining_size -= actual_size
        pos.current_target_idx = target_idx + 1
        pos.partial_exits.append({
            "target_idx": target_idx,
            "price": price,
            "size": actual_size,
            "timestamp": datetime.utcnow().isoformat(),
        })

        if pos.remaining_size <= 0:
            pos.status = "CLOSED"
        else:
            pos.status = "PARTIAL"

        pnl = (price - pos.entry_price) * actual_size if pos.direction == "BUY" else \
              (pos.entry_price - price) * actual_size

        return {
            "action": "PARTIAL_EXIT",
            "epic": pos.epic,
            "price": price,
            "size": actual_size,
            "pnl": round(pnl, 2),
            "remaining": pos.remaining_size,
            "target": target_idx + 1,
        }

    def _execute_full_exit(self, pos: ManagedPosition, price: float, reason: str) -> Dict:
        """Close entire remaining position."""
        logger.info(f"FULL EXIT ({reason}): {pos.epic} @ {price} | Size: {pos.remaining_size}")

        pnl = (price - pos.entry_price) * pos.remaining_size if pos.direction == "BUY" else \
              (pos.entry_price - price) * pos.remaining_size

        pos.partial_exits.append({
            "target_idx": -1,
            "price": price,
            "size": pos.remaining_size,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        })
        pos.remaining_size = 0
        pos.status = "CLOSED"

        # Close on broker
        try:
            self.api.close_position(pos.deal_id)
        except Exception as e:
            logger.error(f"Failed to close position on broker: {e}")

        return {
            "action": "FULL_EXIT",
            "reason": reason,
            "epic": pos.epic,
            "price": price,
            "pnl": round(pnl, 2),
        }

    def _activate_trailing_stop(
        self, pos: ManagedPosition, price: float, ema_value: float
    ):
        """Activate trailing stop based on EMA distance."""
        if pos.direction == "BUY":
            # Trail below the 8 EMA with buffer
            trail_distance = abs(price - ema_value) * 0.5
            pos.trailing_stop = price - trail_distance
        else:
            trail_distance = abs(ema_value - price) * 0.5
            pos.trailing_stop = price + trail_distance

        logger.info(f"{pos.epic}: Trailing stop activated @ {pos.trailing_stop:.2f}")

    def _update_trailing_stop(
        self, pos: ManagedPosition, price: float, ema_value: float
    ):
        """Update trailing stop — only moves in profitable direction."""
        if pos.direction == "BUY":
            new_trail = ema_value * 0.985  # Trail just below 8 EMA
            if new_trail > (pos.trailing_stop or 0):
                pos.trailing_stop = round(new_trail, 2)
                self._update_stop_on_broker(pos)
                logger.info(f"{pos.epic}: Trailing stop raised to {pos.trailing_stop}")
        else:
            new_trail = ema_value * 1.015
            if pos.trailing_stop is None or new_trail < pos.trailing_stop:
                pos.trailing_stop = round(new_trail, 2)
                self._update_stop_on_broker(pos)
                logger.info(f"{pos.epic}: Trailing stop lowered to {pos.trailing_stop}")

    def _update_stop_on_broker(self, pos: ManagedPosition):
        """Push updated stop level to Capital.com."""
        try:
            stop = pos.trailing_stop or pos.stop_price
            self.api.update_position(pos.deal_id, stop_level=stop)
        except Exception as e:
            logger.error(f"Failed to update stop on broker for {pos.epic}: {e}")

    def get_open_positions(self) -> List[Dict]:
        """Return summary of all open/partial positions."""
        return [
            pos.to_dict()
            for pos in self.positions.values()
            if pos.status != "CLOSED"
        ]

    def _save_state(self):
        """Persist position state to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            state = {k: v.to_dict() for k, v in self.positions.items()}
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save position state: {e}")

    def _load_state(self):
        """Load position state from disk on startup."""
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r") as f:
                state = json.load(f)
            for epic, data in state.items():
                if data.get("status") != "CLOSED":
                    self.positions[epic] = ManagedPosition(**data)
            logger.info(f"Loaded {len(self.positions)} active positions from state")
        except Exception as e:
            logger.error(f"Failed to load position state: {e}")
