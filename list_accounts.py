#!/usr/bin/env python3
"""List all Capital.com sub-accounts with IDs and balances."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.api_client import CapitalComClient

api = CapitalComClient(
    "https://demo-api-capital.backend-capital.com",
    os.getenv("CAPITAL_API_KEY"),
    os.getenv("CAPITAL_EMAIL"),
    os.getenv("CAPITAL_PASSWORD"),
)
api.authenticate()

print("\n" + "=" * 60)
print("  YOUR CAPITAL.COM ACCOUNTS")
print("=" * 60)
for acc in api.get_accounts():
    aid = acc.get("accountId", "?")
    name = acc.get("accountName", "?")
    bal = acc.get("balance", {}).get("balance", 0)
    equity = acc.get("balance", {}).get("equity", 0)
    atype = acc.get("accountType", "?")
    print(f"  ID:      {aid}")
    print(f"  Name:    {name}")
    print(f"  Type:    {atype}")
    print(f"  Balance: ${bal:.2f}")
    print(f"  Equity:  ${equity:.2f}")
    print(f"  ---")
print("=" * 60)
print("\nCopy the ID for your SWING TRADING account")
print("and add to .env:  CAPITAL_ACCOUNT_ID=<that ID>")
