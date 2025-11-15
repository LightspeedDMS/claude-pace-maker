#!/usr/bin/env python3
"""
Manual test to demonstrate adaptive throttling algorithm intelligence.

Run this to see how the algorithm calculates delays for various scenarios.
"""

from src.pacemaker.adaptive_throttle import calculate_adaptive_delay


def print_scenario(name, params, description=""):
    """Print scenario results in a readable format."""
    print(f"\n{'='*80}")
    print(f"SCENARIO: {name}")
    if description:
        print(f"Description: {description}")
    print(f"{'='*80}")

    print("\nInput Parameters:")
    print(f"  Current utilization: {params['current_util']:.1f}%")
    print(f"  Target utilization: {params['target_util']:.1f}%")
    print(f"  Overage: {params['current_util'] - params['target_util']:.1f}%")
    print(f"  Time elapsed: {params['time_elapsed_pct']:.1f}%")
    print(f"  Time remaining: {params['time_remaining_hours']:.2f} hours")
    print(f"  Window duration: {params['window_hours']:.0f} hours")

    result = calculate_adaptive_delay(**params)

    print("\nAlgorithm Decision:")
    print(f"  Delay: {result['delay_seconds']} seconds ({result['delay_seconds']/60:.1f} minutes)")
    print(f"  Strategy: {result['strategy']}")

    print("\nProjection:")
    print(f"  Without throttling -> {result['projection']['util_if_no_throttle']:.1f}% at window end")
    print(f"  With throttling    -> {result['projection']['util_if_throttled']:.1f}% at window end")
    print(f"  Estimated tools remaining: {result['projection']['tools_remaining_estimate']}")
    print(f"  Credits remaining: {result['projection']['credits_remaining_pct']:.1f}%")

    print(f"\n{'-'*80}")
    if result['projection']['util_if_no_throttle'] > 100:
        print(f"WARNING: Without throttling, would EXCEED budget by "
              f"{result['projection']['util_if_no_throttle'] - 100:.1f}%!")
    else:
        print(f"INFO: Without throttling, would end at {result['projection']['util_if_no_throttle']:.1f}% (within budget)")


def main():
    """Run manual test scenarios."""
    print("\n" + "="*80)
    print("ADAPTIVE THROTTLING ALGORITHM - MANUAL TEST")
    print("="*80)

    # Current real situation from user
    print_scenario(
        "Current Real Situation",
        {
            'current_util': 56.0,
            'target_util': 32.0,
            'time_elapsed_pct': 31.0,
            'time_remaining_hours': 3.45,
            'window_hours': 5.0,
            'estimated_tools_per_hour': 10.0
        },
        "User's actual situation: 24% over target, mid-window"
    )

    # On track scenario
    print_scenario(
        "On Track",
        {
            'current_util': 50.0,
            'target_util': 50.0,
            'time_elapsed_pct': 50.0,
            'time_remaining_hours': 2.5,
            'window_hours': 5.0,
            'estimated_tools_per_hour': 10.0
        },
        "Exactly on target - no throttling needed"
    )

    # Slight overage early
    print_scenario(
        "Slight Overage Early",
        {
            'current_util': 20.0,
            'target_util': 10.0,
            'time_elapsed_pct': 20.0,
            'time_remaining_hours': 4.0,
            'window_hours': 5.0,
            'estimated_tools_per_hour': 10.0
        },
        "10% over target but plenty of time to correct"
    )

    # Emergency near end
    print_scenario(
        "Emergency Near End",
        {
            'current_util': 95.0,
            'target_util': 50.0,
            'time_elapsed_pct': 80.0,
            'time_remaining_hours': 1.0,
            'window_hours': 5.0,
            'estimated_tools_per_hour': 10.0
        },
        "Way over budget with little time left"
    )

    # 7-day window scenario
    print_scenario(
        "7-Day Window Overage",
        {
            'current_util': 60.0,
            'target_util': 40.0,
            'time_elapsed_pct': 40.0,
            'time_remaining_hours': 100.0,
            'window_hours': 168.0,
            'estimated_tools_per_hour': 10.0
        },
        "20% overage but in 7-day window (more time to correct)"
    )

    # Under budget scenario
    print_scenario(
        "Under Budget",
        {
            'current_util': 30.0,
            'target_util': 50.0,
            'time_elapsed_pct': 50.0,
            'time_remaining_hours': 2.5,
            'window_hours': 5.0,
            'estimated_tools_per_hour': 10.0
        },
        "Using less than target - no throttling needed"
    )

    print("\n" + "="*80)
    print("MANUAL TEST COMPLETE")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
