"""
Capital.com REST API Client
Handles authentication, session management, market data, and order execution.
"""
import time
import json
import logging
import requests
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class CapitalComClient:
    """
    Wrapper for Capital.com REST API v1.

    Authentication flow:
      POST /session with X-CAP-API-KEY header + identifier/password body
      → Returns CST + X-SECURITY-TOKEN headers (valid 10 min after last use)
    """

    def __init__(self, base_url: str, api_key: str, email: str, password: str,
                 account_id: Optional[str] = None):
        self.base_url = f"{base_url}/api/v1"
        self.api_key = api_key
        self.email = email
        self.password = password
        self.account_id = account_id  # Lock to specific sub-account
        self.session = requests.Session()
        self.cst: Optional[str] = None
        self.security_token: Optional[str] = None
        self._last_auth_time: Optional[float] = None
        self._auth_timeout = 540  # Re-auth after 9 min (tokens valid 10 min)

    # ── Authentication ──────────────────────────

    def authenticate(self) -> bool:
        """Create a new trading session."""
        url = f"{self.base_url}/session"
        headers = {"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"identifier": self.email, "password": self.password}

        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                self.cst = resp.headers.get("CST")
                self.security_token = resp.headers.get("X-SECURITY-TOKEN")
                self._last_auth_time = time.time()
                logger.info("Authenticated with Capital.com successfully")

                # Switch to specific account if configured
                if self.account_id:
                    self._switch_account(self.account_id)

                return True
            else:
                logger.error(f"Auth failed: {resp.status_code} - {resp.text}")
                return False
        except requests.RequestException as e:
            logger.error(f"Auth request error: {e}")
            return False

    def _switch_account(self, account_id: str):
        """
        PUT /session - Switch active account to specific sub-account.
        This ensures all trades go to the correct account.
        """
        try:
            resp = self.session.put(
                f"{self.base_url}/session",
                headers={
                    "X-SECURITY-TOKEN": self.security_token,
                    "CST": self.cst,
                    "Content-Type": "application/json",
                },
                json={"accountId": account_id},
                timeout=15,
            )
            if resp.status_code == 200:
                # Update tokens from response (they may change on account switch)
                new_cst = resp.headers.get("CST")
                new_token = resp.headers.get("X-SECURITY-TOKEN")
                if new_cst:
                    self.cst = new_cst
                if new_token:
                    self.security_token = new_token
                logger.info(f"Switched to account: {account_id}")
            else:
                logger.error(
                    f"Account switch failed for {account_id}: "
                    f"{resp.status_code} - {resp.text}"
                )
        except Exception as e:
            logger.error(f"Account switch error: {e}")

    def list_accounts(self) -> List[Dict]:
        """List all available sub-accounts with their IDs and balances."""
        accounts = self.get_accounts()
        for acc in accounts:
            acc_id = acc.get("accountId", "?")
            acc_name = acc.get("accountName", "?")
            balance = acc.get("balance", {}).get("balance", 0)
            acc_type = acc.get("accountType", "?")
            logger.info(
                f"  Account: {acc_id} | Name: {acc_name} | "
                f"Type: {acc_type} | Balance: ${balance:.2f}"
            )
        return accounts

    def _ensure_session(self):
        """Re-authenticate if session is stale (>9 min since last auth)."""
        if (
            self._last_auth_time is None
            or (time.time() - self._last_auth_time) > self._auth_timeout
        ):
            if not self.authenticate():
                raise ConnectionError("Failed to authenticate with Capital.com")

    def _auth_headers(self) -> Dict[str, str]:
        """Return headers with active session tokens."""
        self._ensure_session()
        return {
            "X-SECURITY-TOKEN": self.security_token,
            "CST": self.cst,
            "Content-Type": "application/json",
        }

    # ── Account Info ────────────────────────────

    def get_accounts(self) -> List[Dict]:
        """GET /accounts - Retrieve account details including balance."""
        resp = self.session.get(
            f"{self.base_url}/accounts", headers=self._auth_headers(), timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("accounts", [])

    def get_account_balance(self) -> float:
        """Return balance for the active (or configured) account."""
        accounts = self.get_accounts()
        if not accounts:
            return 0.0

        # If account_id is set, find that specific account
        if self.account_id:
            for acc in accounts:
                if str(acc.get("accountId")) == str(self.account_id):
                    return acc.get("balance", {}).get("balance", 0.0)
            logger.warning(
                f"Account {self.account_id} not found in accounts list. "
                f"Available: {[a.get('accountId') for a in accounts]}"
            )

        # Fallback to first account
        return accounts[0].get("balance", {}).get("balance", 0.0)

    # ── Market Data ─────────────────────────────

    def search_markets(self, search_term: str, limit: int = 20) -> List[Dict]:
        """GET /markets - Search for instruments by name/epic."""
        params = {"searchTerm": search_term, "limit": limit}
        resp = self.session.get(
            f"{self.base_url}/markets",
            headers=self._auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("markets", [])

    def get_market_info(self, epic: str) -> Dict:
        """GET /markets/{epic} - Get detailed market information."""
        resp = self.session.get(
            f"{self.base_url}/markets/{epic}",
            headers=self._auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_prices(
        self,
        epic: str,
        resolution: str = "DAY",
        max_bars: int = 200,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict:
        """
        GET /prices/{epic} - Historical OHLCV candle data.

        Resolutions: MINUTE, MINUTE_5, MINUTE_15, MINUTE_30,
                     HOUR, HOUR_4, DAY, WEEK
        """
        params = {"resolution": resolution, "max": max_bars}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        resp = self.session.get(
            f"{self.base_url}/prices/{epic}",
            headers=self._auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Positions (Trades) ──────────────────────

    def get_positions(self) -> List[Dict]:
        """GET /positions - All open positions."""
        resp = self.session.get(
            f"{self.base_url}/positions", headers=self._auth_headers(), timeout=15
        )
        resp.raise_for_status()
        return resp.json().get("positions", [])

    def create_position(
        self,
        epic: str,
        direction: str,
        size: float,
        stop_level: Optional[float] = None,
        profit_level: Optional[float] = None,
        guaranteed_stop: bool = False,
    ) -> Dict:
        """
        POST /positions - Open a new position.

        Args:
            epic: Instrument identifier (e.g., 'AAPL', 'BTCUSD')
            direction: 'BUY' or 'SELL'
            size: Number of contracts/units
            stop_level: Price for stop loss
            profit_level: Price for take profit
            guaranteed_stop: Whether to use guaranteed stop (extra cost)
        """
        payload = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": guaranteed_stop,
        }
        if stop_level is not None:
            payload["stopLevel"] = stop_level
        if profit_level is not None:
            payload["profitLevel"] = profit_level

        logger.info(f"Opening position: {direction} {size} {epic} | SL={stop_level} TP={profit_level}")

        resp = self.session.post(
            f"{self.base_url}/positions",
            headers=self._auth_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Position opened: {result}")
        return result

    def close_position(self, deal_id: str) -> Dict:
        """DELETE /positions/{dealId} - Close an open position."""
        resp = self.session.delete(
            f"{self.base_url}/positions/{deal_id}",
            headers=self._auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Position closed: {deal_id} -> {result}")
        return result

    def update_position(
        self,
        deal_id: str,
        stop_level: Optional[float] = None,
        profit_level: Optional[float] = None,
    ) -> Dict:
        """PUT /positions/{dealId} - Update stop/profit on existing position."""
        payload = {}
        if stop_level is not None:
            payload["stopLevel"] = stop_level
        if profit_level is not None:
            payload["profitLevel"] = profit_level

        resp = self.session.put(
            f"{self.base_url}/positions/{deal_id}",
            headers=self._auth_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Working Orders (Pending) ────────────────

    def get_working_orders(self) -> List[Dict]:
        """GET /workingorders - All pending orders."""
        resp = self.session.get(
            f"{self.base_url}/workingorders",
            headers=self._auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("workingOrders", [])

    def create_working_order(
        self,
        epic: str,
        direction: str,
        size: float,
        level: float,
        order_type: str = "LIMIT",
        stop_level: Optional[float] = None,
        stop_distance: Optional[float] = None,
        profit_level: Optional[float] = None,
        profit_distance: Optional[float] = None,
        good_till_date: Optional[str] = None,
        guaranteed_stop: bool = False,
    ) -> Dict:
        """
        POST /workingorders - Create a pending order.

        Capital.com working orders API:
        - Requires goodTillDate in ISO format: "YYYY-MM-DDTHH:MM:SS"
        - Supports both stopLevel/profitLevel AND stopDistance/profitDistance
        - stopDistance/profitDistance = number of points from the order level
        - type: 'LIMIT' or 'STOP'

        Args:
            epic: Instrument identifier
            direction: 'BUY' or 'SELL'
            size: Deal size
            level: Trigger price for the order
            order_type: 'LIMIT' or 'STOP'
            stop_level: Absolute price for stop loss
            stop_distance: Distance in points from level for stop loss
            profit_level: Absolute price for take profit
            profit_distance: Distance in points from level for take profit
            good_till_date: ISO datetime string, or None for 30-day default
            guaranteed_stop: Use guaranteed stop (extra spread cost)
        """
        # Build goodTillDate: must be ISO format, NOT "GTC"
        # Default to 30 days from now if not specified
        if good_till_date is None or good_till_date == "GTC":
            gtd = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        else:
            gtd = good_till_date

        payload = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "level": level,
            "type": order_type,
            "goodTillDate": gtd,
            "guaranteedStop": guaranteed_stop,
        }

        # Capital.com supports both absolute levels and distances
        # Try stopDistance/profitDistance first (more reliable for working orders)
        if stop_distance is not None:
            payload["stopDistance"] = stop_distance
        elif stop_level is not None:
            # Convert absolute stop level to distance from order level
            distance = abs(level - stop_level)
            payload["stopDistance"] = round(distance, 2)

        if profit_distance is not None:
            payload["profitDistance"] = profit_distance
        elif profit_level is not None:
            # Convert absolute profit level to distance from order level
            distance = abs(profit_level - level)
            payload["profitDistance"] = round(distance, 2)

        logger.info(
            f"Creating working order: {direction} {size} {epic} @ {level} | "
            f"Type: {order_type} | GTD: {gtd} | Payload: {json.dumps(payload)}"
        )

        resp = self.session.post(
            f"{self.base_url}/workingorders",
            headers=self._auth_headers(),
            json=payload,
            timeout=15,
        )

        if resp.status_code != 200:
            error_body = resp.text
            logger.error(f"Working order failed ({resp.status_code}): {error_body}")

            # Fallback: retry with stopLevel/profitLevel instead of distance
            if "stopDistance" in payload or "profitDistance" in payload:
                logger.info("Retrying with stopLevel/profitLevel instead of distances...")
                payload.pop("stopDistance", None)
                payload.pop("profitDistance", None)
                if stop_level is not None:
                    payload["stopLevel"] = stop_level
                if profit_level is not None:
                    payload["profitLevel"] = profit_level

                resp = self.session.post(
                    f"{self.base_url}/workingorders",
                    headers=self._auth_headers(),
                    json=payload,
                    timeout=15,
                )

                if resp.status_code != 200:
                    logger.error(f"Retry also failed ({resp.status_code}): {resp.text}")
                    resp.raise_for_status()

        result = resp.json()
        logger.info(f"Working order created: {result}")
        return result

    def delete_working_order(self, deal_id: str) -> Dict:
        """DELETE /workingorders/{dealId} - Cancel a pending order."""
        resp = self.session.delete(
            f"{self.base_url}/workingorders/{deal_id}",
            headers=self._auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Session cleanup ─────────────────────────

    def logout(self):
        """DELETE /session - End the current session."""
        try:
            self.session.delete(
                f"{self.base_url}/session", headers=self._auth_headers(), timeout=10
            )
            logger.info("Session closed")
        except Exception as e:
            logger.warning(f"Logout error: {e}")
        finally:
            self.cst = None
            self.security_token = None
