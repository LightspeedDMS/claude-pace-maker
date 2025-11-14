#!/usr/bin/env python3
"""
Probe Claude Code Usage API

Fetches usage and profile data to inspect actual API response structure.
This helps us understand how different account types (Enterprise vs Pro/Max)
return different limit structures.

Usage: python3 scripts/probe-usage-api.py
"""

import json
import sys
from pathlib import Path
import requests
from datetime import datetime


def load_credentials():
    """Load OAuth credentials from Claude Code config"""
    creds_path = Path.home() / ".claude" / ".credentials.json"

    if not creds_path.exists():
        print(f"❌ Credentials not found at: {creds_path}")
        print("   Please run 'claude' command to authenticate first.")
        sys.exit(1)

    try:
        with open(creds_path) as f:
            data = json.load(f)

        oauth = data.get("claudeAiOauth")
        if not oauth:
            print("❌ No OAuth credentials found in credentials file")
            sys.exit(1)

        access_token = oauth.get("accessToken")
        if not access_token:
            print("❌ No access token found")
            sys.exit(1)

        return access_token

    except Exception as e:
        print(f"❌ Failed to load credentials: {e}")
        sys.exit(1)


def fetch_usage(token):
    """Fetch usage data from Claude API"""
    url = "https://api.anthropic.com/api/oauth/usage"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.0.37"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return response.json(), None
        elif response.status_code == 401:
            return None, "Token expired. Run 'claude' to refresh."
        else:
            return None, f"API error: {response.status_code} - {response.text}"

    except requests.exceptions.RequestException as e:
        return None, f"Network error: {e}"


def fetch_profile(token):
    """Fetch profile data from Claude API"""
    url = "https://api.anthropic.com/api/oauth/profile"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.0.37"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return response.json(), None
        else:
            return None, f"API error: {response.status_code} - {response.text}"

    except requests.exceptions.RequestException as e:
        return None, f"Network error: {e}"


def analyze_account_type(profile):
    """Analyze account type from profile data"""
    if not profile:
        return "Unknown"

    account = profile.get("account", {})
    org = profile.get("organization", {})

    org_type = org.get("organization_type", "")
    has_pro = account.get("has_claude_pro", False)
    has_max = account.get("has_claude_max", False)

    if org_type == "claude_enterprise":
        return "Enterprise"
    elif has_max:
        return "Personal Pro Max"
    elif has_pro:
        return "Personal Pro"
    else:
        return "Free/Other"


def print_section(title):
    """Print a section header"""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    print("Claude Code Usage API Probe")
    print(f"Timestamp: {datetime.now().isoformat()}")

    # Load credentials
    print("\n📋 Loading credentials...")
    token = load_credentials()
    print("✅ Credentials loaded")

    # Fetch profile
    print_section("PROFILE DATA")
    profile, error = fetch_profile(token)

    if error:
        print(f"❌ Profile fetch failed: {error}")
    else:
        print(json.dumps(profile, indent=2))

        # Analyze account type
        account_type = analyze_account_type(profile)
        print(f"\n📊 Account Type: {account_type}")

        account = profile.get("account", {})
        org = profile.get("organization", {})

        print(f"   - Email: {account.get('email', 'N/A')}")
        print(f"   - Display Name: {account.get('display_name', 'N/A')}")
        print(f"   - Organization: {org.get('name', 'N/A')}")
        print(f"   - Org Type: {org.get('organization_type', 'N/A')}")
        print(f"   - Rate Limit Tier: {org.get('rate_limit_tier', 'N/A')}")
        print(f"   - Has Pro: {account.get('has_claude_pro', False)}")
        print(f"   - Has Max: {account.get('has_claude_max', False)}")

    # Fetch usage
    print_section("USAGE DATA")
    usage, error = fetch_usage(token)

    if error:
        print(f"❌ Usage fetch failed: {error}")
        sys.exit(1)
    else:
        print(json.dumps(usage, indent=2))

        # Analyze usage structure
        print("\n📊 Usage Structure Analysis:")

        five_hour = usage.get("five_hour")
        if five_hour:
            print(f"\n✅ 5-Hour Limit:")
            print(f"   - Utilization: {five_hour.get('utilization', 0):.1f}%")
            print(f"   - Resets At: {five_hour.get('resets_at', 'N/A')}")
        else:
            print(f"\n❌ 5-Hour Limit: NOT PRESENT")

        seven_day = usage.get("seven_day")
        if seven_day:
            print(f"\n✅ 7-Day Limit:")
            print(f"   - Utilization: {seven_day.get('utilization', 0):.1f}%")
            print(f"   - Resets At: {seven_day.get('resets_at', 'N/A')}")
        else:
            print(f"\n❌ 7-Day Limit: NOT PRESENT (null)")

        seven_day_oauth = usage.get("seven_day_oauth_apps")
        if seven_day_oauth:
            print(f"\n✅ 7-Day OAuth Apps Limit:")
            print(f"   - Utilization: {seven_day_oauth.get('utilization', 0):.1f}%")
        else:
            print(f"\n❌ 7-Day OAuth Apps: NOT PRESENT (null)")

        seven_day_opus = usage.get("seven_day_opus")
        if seven_day_opus:
            print(f"\n✅ 7-Day Opus Limit:")
            print(f"   - Utilization: {seven_day_opus.get('utilization', 0):.1f}%")
        else:
            print(f"\n❌ 7-Day Opus: NOT PRESENT (null)")

    # Summary
    print_section("SUMMARY FOR PACE MAKER")

    if profile:
        account_type = analyze_account_type(profile)
        print(f"Account Type: {account_type}")

    if usage:
        five_hour = usage.get("five_hour")
        seven_day = usage.get("seven_day")

        if five_hour and seven_day:
            print("Pacing Strategy: DUAL-WINDOW")
            print("  - 5-hour window: Logarithmic curve")
            print("  - 7-day window: Linear curve")
            print("  - Pace to most constrained window")
        elif five_hour:
            print("Pacing Strategy: SINGLE-WINDOW")
            print("  - 5-hour window only: Logarithmic curve")
        else:
            print("⚠️  No recognizable rate limit structure!")

    print("\n✅ Probe complete!")


if __name__ == "__main__":
    main()
