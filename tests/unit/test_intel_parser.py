#!/usr/bin/env python3
"""
Unit tests for intel parser module.

Tests parsing of prompt intelligence metadata from assistant responses.
Following TDD: these tests are written FIRST and will fail until implementation.
"""


def test_parse_intel_line_complete():
    """Test parsing complete intel line with all fields."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.8 ◎surg ■bug ◇0.7 ↻2\nActual response content"
    result = parse_intel_line(response)

    assert result is not None
    assert result["frustration"] == 0.8
    assert result["specificity"] == "surg"
    assert result["task_type"] == "bug"
    assert result["quality"] == 0.7
    assert result["iteration"] == 2


def test_parse_intel_line_partial():
    """Test parsing partial intel line with missing fields."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ■feat\nSome content"
    result = parse_intel_line(response)

    assert result is not None
    assert result["frustration"] == 0.5
    assert result["task_type"] == "feat"
    # Missing fields should NOT be present
    assert "specificity" not in result
    assert "quality" not in result
    assert "iteration" not in result


def test_parse_intel_line_no_marker():
    """Test handling response without intel marker."""
    from pacemaker.intel.parser import parse_intel_line

    response = "Regular response without intel line"
    result = parse_intel_line(response)

    assert result is None


def test_parse_intel_line_invalid_frustration_high():
    """Test rejection of frustration value > 1.0."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △1.5 ◎surg ■bug"
    result = parse_intel_line(response)

    # Invalid frustration should be rejected (not included in result)
    assert result is not None
    assert "frustration" not in result
    # Other valid fields should still be parsed
    assert result["specificity"] == "surg"
    assert result["task_type"] == "bug"


def test_parse_intel_line_invalid_frustration_negative():
    """Test rejection of negative frustration value."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △-0.2 ◎surg ■bug"
    result = parse_intel_line(response)

    assert result is not None
    assert "frustration" not in result


def test_parse_intel_line_invalid_specificity():
    """Test rejection of unknown specificity value."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ◎invalid ■bug"
    result = parse_intel_line(response)

    assert result is not None
    assert "specificity" not in result
    assert result["task_type"] == "bug"


def test_parse_intel_line_invalid_task_type():
    """Test rejection of unknown task type."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ◎surg ■invalid"
    result = parse_intel_line(response)

    assert result is not None
    assert "task_type" not in result
    assert result["specificity"] == "surg"


def test_parse_intel_line_invalid_quality_high():
    """Test rejection of quality value > 1.0."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ◎surg ■bug ◇1.8"
    result = parse_intel_line(response)

    assert result is not None
    assert "quality" not in result


def test_parse_intel_line_invalid_quality_negative():
    """Test rejection of negative quality value."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ◎surg ■bug ◇-0.3"
    result = parse_intel_line(response)

    assert result is not None
    assert "quality" not in result


def test_parse_intel_line_invalid_iteration_zero():
    """Test rejection of iteration value 0."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ◎surg ■bug ↻0"
    result = parse_intel_line(response)

    assert result is not None
    assert "iteration" not in result


def test_parse_intel_line_invalid_iteration_high():
    """Test rejection of iteration value > 9."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.5 ◎surg ■bug ↻12"
    result = parse_intel_line(response)

    assert result is not None
    # Should not parse multi-digit iterations
    assert "iteration" not in result


def test_parse_intel_line_all_specificities():
    """Test all valid specificity values."""
    from pacemaker.intel.parser import parse_intel_line

    for spec in ["surg", "const", "outc", "expl"]:
        response = f"§ ◎{spec}"
        result = parse_intel_line(response)
        assert result is not None
        assert result["specificity"] == spec


def test_parse_intel_line_all_task_types():
    """Test all valid task type values."""
    from pacemaker.intel.parser import parse_intel_line

    for task in [
        "bug",
        "feat",
        "refac",
        "research",
        "test",
        "docs",
        "debug",
        "conf",
        "other",
    ]:
        response = f"§ ■{task}"
        result = parse_intel_line(response)
        assert result is not None
        assert result["task_type"] == task


def test_parse_intel_line_multiline_response():
    """Test intel line can be on any line (not just first)."""
    from pacemaker.intel.parser import parse_intel_line

    response = "Some content\n§ △0.5 ■feat\nMore content"
    result = parse_intel_line(response)

    assert result is not None
    assert result["frustration"] == 0.5
    assert result["task_type"] == "feat"


def test_strip_intel_line_removes_marker():
    """Test stripping intel line from output."""
    from pacemaker.intel.parser import strip_intel_line

    text = "§ △0.8 ◎surg ■bug ◇0.7 ↻2\nActual response content"
    result = strip_intel_line(text)

    assert "§" not in result
    assert "Actual response content" in result


def test_strip_intel_line_preserves_content():
    """Test that non-intel content is preserved."""
    from pacemaker.intel.parser import strip_intel_line

    text = "Line 1\n§ △0.5 ■feat\nLine 2\nLine 3"
    result = strip_intel_line(text)

    assert "Line 1" in result
    assert "Line 2" in result
    assert "Line 3" in result
    assert "§" not in result


def test_strip_intel_line_handles_no_marker():
    """Test stripping when no intel marker present."""
    from pacemaker.intel.parser import strip_intel_line

    text = "Regular content\nNo intel here"
    result = strip_intel_line(text)

    assert result == text  # Should be unchanged


def test_parse_intel_line_edge_case_zero_frustration():
    """Test that frustration 0.0 is valid."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △0.0 ■feat"
    result = parse_intel_line(response)

    assert result is not None
    assert result["frustration"] == 0.0


def test_parse_intel_line_edge_case_one_frustration():
    """Test that frustration 1.0 is valid."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ △1.0 ■feat"
    result = parse_intel_line(response)

    assert result is not None
    assert result["frustration"] == 1.0


def test_parse_intel_line_edge_case_iteration_one():
    """Test that iteration 1 is valid."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ ↻1 ■feat"
    result = parse_intel_line(response)

    assert result is not None
    assert result["iteration"] == 1


def test_parse_intel_line_edge_case_iteration_nine():
    """Test that iteration 9 is valid."""
    from pacemaker.intel.parser import parse_intel_line

    response = "§ ↻9 ■feat"
    result = parse_intel_line(response)

    assert result is not None
    assert result["iteration"] == 9
