#!/usr/bin/env python3
"""
Capital.com Authentication Diagnostic
Run this to pinpoint exactly why auth is failing.

Usage:
  python3 test_auth.py

It will prompt you for credentials interactively (no escaping issues).
"""
import requests
import json
import getpass
import sys


def test_auth(base_url, api_key, email, password, label):
    """Test authentication against a Capital.com endpoint."""
    url = f"{base_url}/api/v1/session"
    headers = {
        "X-CAP-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "identifier": email,
        "password": password,
    }

    print(f"\n{'='*50}")
    print(f"  Testing: {label}")
    print(f"  URL:     {url}")
    print(f"  Email:   {email}")
    print(f"  API Key: {api_key[:8]}...{api_key[-4:]}")
    print(f"  Pass:    {'*' * len(password)} ({len(password)} chars)")
    print(f"{'='*50}")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"  Status:  {resp.status_code}")

        if resp.status_code == 200:
            cst = resp.headers.get("CST", "")
            token = resp.headers.get("X-SECURITY-TOKEN", "")
            print(f"  CST:     {cst[:20]}..." if cst else "  CST:     (missing)")
            print(f"  Token:   {token[:20]}..." if token else "  Token:   (missing)")
            print(f"  ✅ SUCCESS — Authentication works!")

            # Try to get account info
            auth_headers = {
                "X-SECURITY-TOKEN": token,
                "CST": cst,
                "Content-Type": "application/json",
            }
            acc_resp = requests.get(f"{base_url}/api/v1/accounts", headers=auth_headers, timeout=15)
            if acc_resp.status_code == 200:
                accounts = acc_resp.json().get("accounts", [])
                for acc in accounts:
                    bal = acc.get("balance", {})
                    print(f"  Account: {acc.get('accountId')} | Balance: ${bal.get('balance', 0):.2f}")
            return True
        else:
            body = resp.text
            print(f"  Body:    {body}")

            # Diagnose specific errors
            if "invalid.details" in body:
                print(f"  ❌ DIAGNOSIS: Email or password is wrong for this endpoint.")
                print(f"     → Capital.com DEMO and LIVE have SEPARATE logins.")
                print(f"     → If your API key is from DEMO, your email/password")
                print(f"       must be the ones you used to create the DEMO account.")
            elif "invalid.apikey" in body or "api-key" in body.lower():
                print(f"  ❌ DIAGNOSIS: API key is invalid or doesn't match this endpoint.")
                print(f"     → A DEMO API key only works with the DEMO endpoint.")
                print(f"     → A LIVE API key only works with the LIVE endpoint.")
            elif "security" in body.lower():
                print(f"  ❌ DIAGNOSIS: Security block — possibly IP restriction or 2FA.")
            else:
                print(f"  ❌ DIAGNOSIS: Unknown error. Check the body above.")

            return False

    except requests.exceptions.ConnectionError as e:
        print(f"  ❌ Connection failed: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def test_without_api_key(base_url, email, password, label):
    """
    Capital.com also supports auth WITHOUT an API key for some account types.
    The API key goes in the header, but it's not always required.
    """
    url = f"{base_url}/api/v1/session"
    headers = {"Content-Type": "application/json"}
    payload = {"identifier": email, "password": password}

    print(f"\n  Testing WITHOUT API key ({label})...")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"  Status: {resp.status_code} | Body: {resp.text[:200]}")
        if resp.status_code == 200:
            print(f"  ✅ Works WITHOUT API key!")
            return True
    except Exception as e:
        print(f"  Error: {e}")
    return False


def main():
    print("╔══════════════════════════════════════════════╗")
    print("║  Capital.com Authentication Diagnostic Tool  ║")
    print("╚══════════════════════════════════════════════╝")
    print()
    print("This tool tests your credentials against both")
    print("DEMO and LIVE endpoints to find what works.")
    print()
    print("Enter your credentials below (password is hidden):")
    print()

    email = input("  Email: ").strip()
    password = getpass.getpass("  Password: ")
    api_key = input("  API Key: ").strip()

    DEMO = "https://demo-api-capital.backend-capital.com"
    LIVE = "https://api-capital.backend-capital.com"

    print("\n\n" + "=" * 50)
    print("  RUNNING 4 TESTS...")
    print("=" * 50)

    results = {}

    # Test 1: Demo with API key
    results["demo_with_key"] = test_auth(DEMO, api_key, email, password, "DEMO + API Key")

    # Test 2: Live with API key
    results["live_with_key"] = test_auth(LIVE, api_key, email, password, "LIVE + API Key")

    # Test 3: Demo without API key
    results["demo_no_key"] = test_without_api_key(DEMO, email, password, "DEMO")

    # Test 4: Live without API key
    results["live_no_key"] = test_without_api_key(LIVE, email, password, "LIVE")

    # Summary
    print("\n\n" + "=" * 50)
    print("  RESULTS SUMMARY")
    print("=" * 50)
    for test, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test:25s} {status}")

    if not any(results.values()):
        print("\n  ⚠️  ALL TESTS FAILED. Common fixes:")
        print()
        print("  1. SEPARATE ACCOUNTS: Capital.com Demo and Live")
        print("     are completely separate. You may have signed up")
        print("     for Demo with a different email or password.")
        print("     → Go to https://demo-capital.com and try logging in")
        print()
        print("  2. PASSWORD SPECIAL CHARACTERS: If your password has")
        print("     $, !, \\, or other special chars, try resetting it")
        print("     to something simple like 'TradingBot2026!' temporarily.")
        print()
        print("  3. API KEY MISMATCH: Generate a NEW API key from the")
        print("     same account (Demo or Live) you're trying to connect to.")
        print("     → Capital.com → Settings → API → Generate New Key")
        print()
        print("  4. ENCRYPTION/REGION: Some Capital.com regions (e.g. .com/ae)")
        print("     use different API endpoints. Check your account region.")
    else:
        working = [k for k, v in results.items() if v]
        print(f"\n  ✅ Working config: {', '.join(working)}")
        if "demo_with_key" in working:
            print(f"\n  Set in your .env:")
            print(f"    USE_DEMO=true")
        elif "live_with_key" in working:
            print(f"\n  Set in your .env:")
            print(f"    USE_DEMO=false")


if __name__ == "__main__":
    main()
