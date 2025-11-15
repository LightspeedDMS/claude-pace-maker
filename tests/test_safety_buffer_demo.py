#!/usr/bin/env python3
"""
Demonstration script for 95% safety buffer feature.

Shows how the safety buffer prevents hitting hard credit limits.
"""

from datetime import datetime
from src.pacemaker.adaptive_throttle import calculate_adaptive_delay


def demo_safety_buffer():
    """Demonstrate the 95% safety buffer in action."""
    print("=" * 70)
    print("95% Safety Buffer Demonstration")
    print("=" * 70)
    print()

    # Scenario: Wednesday noon in a Mon-Sun window
    # Allowance at Wednesday noon: ~50% (2.5 days / 5 weekdays)
    window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
    current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

    scenarios = [
        {
            "name": "Under Safety Buffer (45% usage)",
            "current_util": 45.0,
            "description": "Usage is 90% of allowance - under 95% threshold"
        },
        {
            "name": "At Safety Buffer (47.5% usage)",
            "current_util": 47.5,
            "description": "Usage is exactly 95% of allowance"
        },
        {
            "name": "Over Safety Buffer (48% usage)",
            "current_util": 48.0,
            "description": "Usage is 96% of allowance - exceeds safety buffer"
        },
        {
            "name": "Significantly Over (52% usage)",
            "current_util": 52.0,
            "description": "Usage is 104% of allowance - well over safety buffer"
        }
    ]

    for scenario in scenarios:
        print(f"\n{scenario['name']}")
        print(f"  {scenario['description']}")
        print("-" * 70)

        result = calculate_adaptive_delay(
            current_util=scenario['current_util'],
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,  # ~4 days left
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        proj = result['projection']
        print(f"  Raw Allowance:     {proj['allowance']:.1f}%")
        print(f"  Safe Allowance:    {proj['safe_allowance']:.1f}% (95% of {proj['allowance']:.1f}%)")
        print(f"  Current Usage:     {scenario['current_util']:.1f}%")
        print(f"  Buffer Remaining:  {proj['buffer_remaining']:+.1f}%")
        print(f"  Delay:             {result['delay_seconds']} seconds")
        print(f"  Strategy:          {result['strategy']}")
        print(f"  Projected End:     {proj['util_if_throttled']:.1f}%")
        print()

    print("=" * 70)
    print("Key Insight:")
    print("  - Without safety buffer: throttles at 50% of allowance")
    print("  - With 95% safety buffer: throttles at 47.5% of allowance")
    print("  - Provides 2.5% headroom to prevent hitting hard limits")
    print("=" * 70)


def demo_custom_buffers():
    """Demonstrate custom safety buffer percentages."""
    print("\n\n")
    print("=" * 70)
    print("Custom Safety Buffer Demonstration")
    print("=" * 70)
    print()

    window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
    current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon
    current_util = 48.0

    buffers = [
        (90.0, "Conservative (10% headroom)"),
        (95.0, "Default (5% headroom)"),
        (98.0, "Aggressive (2% headroom)"),
        (100.0, "No buffer (original behavior)")
    ]

    print(f"Current usage: {current_util}%")
    print(f"Raw allowance: ~50% (Wednesday noon)")
    print()

    for buffer_pct, description in buffers:
        result = calculate_adaptive_delay(
            current_util=current_util,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=buffer_pct
        )

        safe_allowance = result['projection']['safe_allowance']
        delay = result['delay_seconds']
        strategy = result['strategy']

        print(f"{buffer_pct}% buffer - {description}")
        print(f"  Safe threshold: {safe_allowance:.1f}%")
        print(f"  Throttle? {'YES' if delay > 0 else 'NO'} (delay: {delay}s, strategy: {strategy})")
        print()

    print("=" * 70)


if __name__ == '__main__':
    demo_safety_buffer()
    demo_custom_buffers()
