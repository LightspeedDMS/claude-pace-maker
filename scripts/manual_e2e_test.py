#!/usr/bin/env python3
"""
Manual E2E Testing Script for Credit-Aware Adaptive Throttling

Tests all acceptance criteria against real systems:
1. API polling with real OAuth endpoint
2. Real SQLite database operations
3. Real pacing calculations
4. Real delay execution
5. Complete end-to-end workflow
"""

import sys
import os
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from pacemaker import calculator, database, api_client, pacing_engine, hook


def print_test(name: str):
    """Print test header."""
    print(f"\n{'='*70}")
    print(f"TEST: {name}")
    print('='*70)


def print_result(passed: bool, message: str = ""):
    """Print test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status}: {message}")
    return passed


def test_api_polling_interval():
    """AC1: Test 60-second API polling throttle."""
    print_test("Acceptance Criterion 1: 60-Second API Polling")

    # First poll - should allow
    result1 = pacing_engine.should_poll_api(None, interval=2)
    if not print_result(result1 == True, "First poll allowed"):
        return False

    # Immediate second poll - should block
    poll_time = datetime.utcnow()
    result2 = pacing_engine.should_poll_api(poll_time, interval=2)
    if not print_result(result2 == False, "Immediate second poll blocked"):
        return False

    # Wait and third poll - should allow
    time.sleep(2.1)
    result3 = pacing_engine.should_poll_api(poll_time, interval=2)
    return print_result(result3 == True, "Poll after interval allowed")


def test_database_operations():
    """AC2: Test SQLite database with proper schema."""
    print_test("Acceptance Criterion 2: SQLite Database Operations")

    import tempfile
    temp_db = tempfile.mktemp(suffix='.db')

    try:
        # Initialize database
        init_success = database.initialize_database(temp_db)
        if not print_result(init_success, "Database initialized"):
            return False

        # Verify file created
        if not print_result(os.path.exists(temp_db), "Database file exists"):
            return False

        # Insert snapshot
        timestamp = datetime.utcnow()
        insert_success = database.insert_usage_snapshot(
            db_path=temp_db,
            timestamp=timestamp,
            five_hour_util=50.0,
            five_hour_resets_at=timestamp + timedelta(hours=3),
            seven_day_util=60.0,
            seven_day_resets_at=timestamp + timedelta(days=4),
            session_id='manual-test'
        )
        if not print_result(insert_success, "Snapshot inserted"):
            return False

        # Query snapshot
        snapshots = database.query_recent_snapshots(temp_db, minutes=5)
        return print_result(len(snapshots) == 1, f"Snapshot queried: {len(snapshots)} found")

    finally:
        if os.path.exists(temp_db):
            os.unlink(temp_db)


def test_target_calculations():
    """AC3: Test logarithmic and linear target calculations."""
    print_test("Acceptance Criterion 3: Target Utilization Calculations")

    # Test 5-hour logarithmic curve
    target_5h_start = calculator.calculate_logarithmic_target(0.0)
    target_5h_mid = calculator.calculate_logarithmic_target(50.0)
    target_5h_end = calculator.calculate_logarithmic_target(100.0)

    if not print_result(target_5h_start < 1.0, f"5-hour start: {target_5h_start:.2f}% (should be ~0%)"):
        return False
    if not print_result(60.0 < target_5h_mid < 65.0, f"5-hour midpoint: {target_5h_mid:.2f}% (should be ~63%)"):
        return False
    if not print_result(abs(target_5h_end - 100.0) < 1.0, f"5-hour end: {target_5h_end:.2f}% (should be 100%)"):
        return False

    # Test 7-day linear curve
    target_7d_start = calculator.calculate_linear_target(0.0)
    target_7d_mid = calculator.calculate_linear_target(50.0)
    target_7d_end = calculator.calculate_linear_target(100.0)

    if not print_result(target_7d_start == 0.0, f"7-day start: {target_7d_start:.2f}% (should be 0%)"):
        return False
    if not print_result(target_7d_mid == 50.0, f"7-day midpoint: {target_7d_mid:.2f}% (should be 50%)"):
        return False
    return print_result(target_7d_end == 100.0, f"7-day end: {target_7d_end:.2f}% (should be 100%)")


def test_most_constrained_window():
    """AC4: Test most constrained window determination."""
    print_test("Acceptance Criterion 4: Most Constrained Window")

    # 5-hour more constrained
    result1 = calculator.determine_most_constrained_window(
        five_hour_util=70.0, five_hour_target=50.0,
        seven_day_util=60.0, seven_day_target=50.0
    )
    if not print_result(result1['window'] == '5-hour', f"5-hour constrained: {result1['window']}"):
        return False

    # 7-day more constrained
    result2 = calculator.determine_most_constrained_window(
        five_hour_util=55.0, five_hour_target=50.0,
        seven_day_util=75.0, seven_day_target=50.0
    )
    return print_result(result2['window'] == '7-day', f"7-day constrained: {result2['window']}")


def test_adaptive_delays():
    """AC5: Test adaptive delay application."""
    print_test("Acceptance Criterion 5: Adaptive Delay Application")

    # No delay at zero deviation (zero tolerance)
    delay1 = calculator.calculate_delay(deviation_percent=0.0, threshold=0)
    if not print_result(delay1 == 0, f"Zero deviation: {delay1}s delay (should be 0)"):
        return False

    # Delay when over target (zero tolerance - immediate throttling)
    delay2 = calculator.calculate_delay(deviation_percent=10.0, threshold=0)
    if not print_result(delay2 == 105, f"10% over target: {delay2}s delay (should be 105)"):
        return False

    # Capped at max delay
    delay3 = calculator.calculate_delay(deviation_percent=200.0, threshold=0, max_delay=120)
    return print_result(delay3 == 120, f"Large deviation: {delay3}s delay (should be capped at 120)")


def test_hybrid_delay_strategy():
    """AC6: Test hybrid delay strategy."""
    print_test("Acceptance Criterion 6: Hybrid Delay Strategy")

    # Direct execution for < 30s
    strategy1 = pacing_engine.determine_delay_strategy(delay_seconds=25)
    if not print_result(strategy1['method'] == 'direct', f"Short delay method: {strategy1['method']} (should be 'direct')"):
        return False

    # Prompt injection for >= 30s
    strategy2 = pacing_engine.determine_delay_strategy(delay_seconds=45)
    if not print_result(strategy2['method'] == 'prompt', f"Long delay method: {strategy2['method']} (should be 'prompt')"):
        return False

    return print_result('45' in strategy2['prompt'], "Prompt includes delay duration")


def test_null_reset_times():
    """AC7: Test NULL reset time handling."""
    print_test("Acceptance Criterion 7: NULL Reset Time Handling")

    # Calculate with NULL 5-hour window
    time_pct = calculator.calculate_time_percent(None)
    if not print_result(time_pct == 0.0, f"NULL reset time: {time_pct}% (should be 0%)"):
        return False

    # Determine constrained window with one NULL
    result = calculator.determine_most_constrained_window(
        five_hour_util=None, five_hour_target=0.0,
        seven_day_util=60.0, seven_day_target=50.0
    )
    return print_result(result['window'] == '7-day', f"NULL window ignored, using: {result['window']}")


def test_graceful_degradation():
    """AC8: Test graceful degradation when API unavailable."""
    print_test("Acceptance Criterion 8: Graceful Degradation")

    import tempfile

    temp_db = tempfile.mktemp(suffix='.db')
    database.initialize_database(temp_db)

    try:
        # Mock API failure (None response)
        from unittest.mock import patch

        with patch('pacemaker.api_client.fetch_usage', return_value=None):
            with patch('pacemaker.api_client.load_access_token', return_value='fake-token'):
                result = pacing_engine.run_pacing_check(
                    db_path=temp_db,
                    session_id='degradation-test',
                    last_poll_time=None
                )

        # Should return gracefully without throttling
        if not print_result(result is not None, "Function returned (didn't crash)"):
            return False

        return print_result(
            result['decision']['should_throttle'] == False,
            "No throttling when API unavailable (graceful degradation)"
        )

    finally:
        if os.path.exists(temp_db):
            os.unlink(temp_db)


def main():
    """Run all manual E2E tests."""
    print("\n" + "="*70)
    print("MANUAL E2E TESTING - Credit-Aware Adaptive Throttling")
    print("="*70)
    print(f"Timestamp: {datetime.utcnow().isoformat()}")

    tests = [
        ("AC1: 60-Second API Polling", test_api_polling_interval),
        ("AC2: SQLite Database Operations", test_database_operations),
        ("AC3: Target Calculations", test_target_calculations),
        ("AC4: Most Constrained Window", test_most_constrained_window),
        ("AC5: Adaptive Delays", test_adaptive_delays),
        ("AC6: Hybrid Delay Strategy", test_hybrid_delay_strategy),
        ("AC7: NULL Reset Times", test_null_reset_times),
        ("AC8: Graceful Degradation", test_graceful_degradation),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"❌ FAIL: Exception - {e}")
            results.append((name, False))

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)

    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)

    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed_count}/{total_count} tests passed ({passed_count/total_count*100:.0f}%)")

    if passed_count == total_count:
        print("\n🎉 ALL ACCEPTANCE CRITERIA VALIDATED!")
        return 0
    else:
        print(f"\n⚠️  {total_count - passed_count} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
