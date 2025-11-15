# 12-Hour Preload Allowance System

## Overview

The 12-hour preload system solves the "day-1 throttling hell" problem by giving users a buffer of credit allowance at the start of each 7-day window. Instead of starting at 0% allowance, users get 10% immediately, allowing full-speed work for approximately 12 weekday hours.

## Problem Statement

### Without Preload: The Day-1 Throttling Problem

When a 7-day credit window resets, the standard linear accrual starts at 0%:

**Friday 4:00 PM** (Window Resets):
- Allowance: 0%
- User starts working immediately
- After 1 hour of work: Usage = 2%, Allowance = 0.83%
- **Result**: System throttles immediately with 350s delays

This makes the first day after reset completely unusable - users are throttled from the very first hour.

### Root Cause

**Linear Accrual Math**:
```
Allowance per hour = 100% ÷ 120 weekday hours = 0.833% per hour
```

At window start:
- Hour 0: 0% allowance
- Hour 1: 0.83% allowance
- Hour 2: 1.67% allowance
- Hour 4: 3.33% allowance

But typical usage in hour 1 might be 2-3%, immediately exceeding allowance.

## Solution: 12-Hour Preload

### Concept

Give users a **startup credit allowance** of 10% (equivalent to 12 weekday hours) at the moment the window resets.

**Benefits**:
1. No throttling for first 12 weekday hours (unless usage exceeds 9.5%)
2. Users can work at full speed on day 1
3. After preload ends, normal accrual takes over seamlessly

### Algorithm

```python
PRELOAD_HOURS = 12.0  # Configurable
TOTAL_WEEKDAY_HOURS = 120.0  # 5 days × 24 hours
ACCRUAL_RATE = 100.0 / 120.0  # 0.833% per weekday hour

weekday_hours_elapsed = count_weekday_seconds(window_start, now) / 3600.0
preload_allowance = (PRELOAD_HOURS / TOTAL_WEEKDAY_HOURS) * 100  # 10%

if weekday_hours_elapsed <= PRELOAD_HOURS:
    return preload_allowance  # Flat 10%
else:
    return weekday_hours_elapsed * ACCRUAL_RATE  # Normal accrual
```

### Two-Phase Allowance Curve

**Phase 1: Preload (0-12 weekday hours)**
- Allowance: **Flat 10%**
- Duration: First 12 weekday hours (excludes weekends)
- Behavior: No accrual, stays constant at 10%

**Phase 2: Normal Accrual (after 12 weekday hours)**
- Allowance: Linear growth from current level
- Formula: `weekday_hours × 0.833%`
- Behavior: Grows 0.833% per weekday hour

## Worked Examples

### Example 1: Window Starts Monday Morning

**Timeline**:

| Time | Weekday Hours | Without Preload | With Preload | Difference |
|------|---------------|----------------|--------------|------------|
| Mon 12 AM (start) | 0h | 0% | **10%** | +10% |
| Mon 4 AM | 4h | 3.3% | **10%** | +6.7% |
| Mon 8 AM | 8h | 6.7% | **10%** | +3.3% |
| Mon 12 PM | 12h | 10% | **10%** | 0% (transition) |
| Mon 4 PM | 16h | 13.3% | **13.3%** | 0% (normal) |
| Tue 4 PM | 40h | 33.3% | **33.3%** | 0% (normal) |
| Fri 12 AM | 120h | 100% | **100%** | 0% (normal) |

**Key Insight**: Preload gives maximum benefit in first 8 hours (+6.7% to +10%), then converges with normal accrual.

### Example 2: Window Starts Friday Afternoon (Spans Weekend)

**Timeline**:

| Time | Weekday Hours | Calendar Hours | Allowance | Notes |
|------|---------------|----------------|-----------|-------|
| **Fri 4 PM** (start) | 0h | 0h | **10%** | Preload activated |
| **Fri 8 PM** | 4h | 4h | **10%** | Still in preload |
| **Sat 12 AM** | 8h | 8h | **10%** | Weekend starts, stays in preload |
| **Sat 12 PM** | 8h | 20h | **10%** | Weekend frozen (8 weekday hours) |
| **Sun 6 PM** | 8h | 50h | **10%** | Weekend frozen |
| **Mon 4 AM** | 12h | 60h | **10%** | Preload ends (12 weekday hours) |
| **Mon 8 AM** | 16h | 64h | **13.3%** | Normal accrual starts |
| **Mon 4 PM** | 24h | 72h | **20%** | Normal accrual |

**Critical Point**: Preload ends **Monday 4 AM** (12 weekday hours after Friday 4 PM), NOT Saturday 4 AM (which would only be 8 calendar hours into the window).

### Example 3: Heavy Usage on Day 1

**Scenario**: User works intensively Friday evening

| Time | Weekday Hours | Allowance | Usage | Safe Allowance (95%) | Status |
|------|---------------|-----------|-------|---------------------|--------|
| Fri 4 PM | 0h | 10% | 0% | 9.5% | ✓ OK |
| Fri 6 PM | 2h | 10% | 5% | 9.5% | ✓ OK |
| Fri 8 PM | 4h | 10% | 8% | 9.5% | ✓ OK |
| Fri 10 PM | 6h | 10% | 10% | 9.5% | ⚠️ THROTTLE (10% > 9.5%) |

**Result**: User can consume up to 9.5% in first 12 weekday hours before throttling kicks in. This is much better than the old 0% allowance at start.

## Implementation Details

### Function Signature

```python
def calculate_allowance_pct(
    window_start: datetime,
    current_time: datetime,
    window_hours: float = 168.0,
    preload_hours: float = 0.0  # Default: no preload (backward compatible)
) -> float:
```

### Configuration Integration

**Configuration File** (`~/.claude-pace-maker/config.json`):
```json
{
  "preload_hours": 12.0
}
```

**Data Flow**:
```
config.json → hook.py → pacing_engine.py → adaptive_throttle.py
```

### Backward Compatibility

Setting `preload_hours=0.0` restores original behavior:
- Allowance starts at 0%
- Linear accrual from the beginning
- No preload buffer

All existing tests pass with this configuration.

## Benefits Analysis

### Quantitative Benefits

**Day 1 Comparison** (First 8 hours):

| Metric | Without Preload | With 12h Preload | Improvement |
|--------|----------------|------------------|-------------|
| Allowance at hour 0 | 0% | 10% | +10% |
| Allowance at hour 4 | 3.3% | 10% | +6.7% |
| Allowance at hour 8 | 6.7% | 10% | +3.3% |
| Throttling on day 1 | Immediate | None (if usage < 9.5%) | Major |
| Usability | Poor | Excellent | Major |

### Qualitative Benefits

1. **Prevents Throttling Hell**: Users don't experience immediate throttling when window resets
2. **Natural Work Rhythm**: First day feels normal, not artificially constrained
3. **Safety Net**: Combined with 95% safety buffer, gives 9.5% working space on day 1
4. **Smooth Transition**: After preload ends, algorithm continues naturally

## Edge Cases

### Edge Case 1: Window Resets Mid-Work Session

**Scenario**: User is working when window resets

**Behavior**:
- Old window ends, new window starts
- Allowance immediately jumps to 10% (preload)
- User can continue working at full speed
- No throttling interruption

### Edge Case 2: Very Short Window (24 hours)

**Setup**: 24-hour window with 12-hour preload

**Calculation**:
- Total weekday hours: 24h (if all weekday)
- Preload: (12h / 24h) × 100 = **50%**

**Result**: First 12 hours get 50% allowance (half the window).

### Edge Case 3: Window Starts on Weekend

**Setup**: Window starts Saturday 12 PM

**Behavior**:
- Saturday + Sunday: 0 weekday hours
- Monday 12 AM: Preload activated (0 weekday hours elapsed)
- Monday 12 PM: 12 weekday hours → end of preload
- Preload effectively applies to first 12 hours of Monday

**Allowance**:
- Saturday/Sunday: 10% (preload, even though no weekday hours yet)
- Monday 12 PM: 10% (end of preload)
- Monday 1 PM: 10.83% (normal accrual starts)

## Testing

### Test Coverage

**9 comprehensive tests** in `tests/test_adaptive_throttle.py`:

1. `test_preload_at_window_start` - Hour 0 returns 10%
2. `test_preload_at_4_hours` - Hour 4 returns 10%
3. `test_preload_ends_at_12_hours` - Hour 12 returns 10%
4. `test_normal_accrual_after_preload` - Hour 16 returns 13.3%
5. `test_preload_across_weekend` - Friday + weekend + Monday = 12 weekday hours
6. `test_accrual_after_weekend` - 24 weekday hours = 20%
7. `test_custom_preload_hours` - 24h preload = 20%
8. `test_zero_preload_backward_compatible` - preload=0 = original behavior
9. `test_preload_with_safety_buffer` - Preload works with 95% buffer

**All tests pass**: 67/67 (100%)

## Configuration Guide

### Recommended Settings

**Default (Recommended)**:
```json
{
  "preload_hours": 12.0
}
```
- Provides 10% starting allowance
- Balances usability with safety
- Works well for typical usage patterns

**Conservative**:
```json
{
  "preload_hours": 6.0
}
```
- Provides 5% starting allowance
- More cautious approach
- Faster convergence to normal accrual

**Generous**:
```json
{
  "preload_hours": 24.0
}
```
- Provides 20% starting allowance
- Full first day at full speed
- Takes longer to converge

**Original Behavior**:
```json
{
  "preload_hours": 0.0
}
```
- No preload
- Starts at 0% allowance
- Backward compatible

## Comparison with Other Approaches

### Alternative 1: Higher Safety Buffer

**Approach**: Use 90% safety buffer instead of preload
**Problem**: Doesn't solve day-1 issue (still starts at 0% allowance)
**Verdict**: Complementary, not a replacement

### Alternative 2: Ignore First N Hours

**Approach**: Don't throttle for first N hours
**Problem**: Could exceed limits if usage is heavy
**Verdict**: Less safe than preload (no actual budget allocation)

### Alternative 3: Exponential Accrual

**Approach**: Exponential curve that starts higher
**Problem**: Complex, hard to reason about, ends below 100%
**Verdict**: Unnecessary complexity

### Why Preload is Better

1. **Simple**: Flat percentage for first N hours
2. **Safe**: Actual budget allocation (10% of total)
3. **Predictable**: Users know they have 10% to work with
4. **Flexible**: Configurable for different needs

## Conclusion

The 12-hour preload system successfully solves the day-1 throttling problem while maintaining safety and simplicity. Combined with weekend-aware accrual and 95% safety buffer, it provides:

- **Usability**: Full-speed work for first 12 weekday hours
- **Safety**: 10% allowance with 9.5% safe threshold
- **Accuracy**: Weekend-aware, reflects actual work patterns
- **Flexibility**: Configurable for different usage patterns

The implementation is production-ready, thoroughly tested (67/67 tests pass), and backward compatible.

---

**Document Version**: 1.0
**Last Updated**: 2025-11-14
