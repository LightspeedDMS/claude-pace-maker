#!/usr/bin/env python3
"""
Unit tests for intent_validator module.

Tests prompt template loading from organized subfolders.
"""


def test_get_pre_tool_prompt_template_uses_new_path():
    """Test that pre-tool prompt is loaded from pre_tool_use/ subfolder."""
    from pacemaker.intent_validator import get_pre_tool_prompt_template

    # Execute: Load template
    template = get_pre_tool_prompt_template()

    # Assert: Template loaded successfully (would raise FileNotFoundError if path wrong)
    assert template is not None
    assert isinstance(template, str)
    assert len(template) > 0
    # Verify it contains expected content
    assert "OUTCOME" in template or "tool_name" in template


def test_get_stop_hook_prompt_template_uses_new_path():
    """Test that stop hook prompt is loaded from stop/ subfolder."""
    from pacemaker.intent_validator import get_prompt_template

    # Execute: Load template
    template = get_prompt_template()

    # Assert: Template loaded successfully
    assert template is not None
    assert isinstance(template, str)
    assert len(template) > 0
    # Verify it contains expected content
    assert "APPROVED" in template or "BLOCKED" in template


def test_build_intent_declaration_prompt_uses_external_template():
    """Test that intent declaration prompt uses external template with variables."""
    from pacemaker.intent_validator import _build_intent_declaration_prompt

    # Setup: Test data
    messages = ["Message 1: Test message", "Message 2: Another message"]
    file_path = "src/test.py"
    tool_name = "Write"

    # Execute: Build prompt
    prompt = _build_intent_declaration_prompt(messages, file_path, tool_name)

    # Assert: Prompt contains replaced variables
    assert "test.py" in prompt  # filename extracted
    assert "Write" in prompt  # tool_name
    assert "Message 1: Test message" in prompt  # messages included
    assert "create or modify" in prompt  # action for Write tool
    # Verify template structure present
    assert "intent" in prompt.lower()
    assert "declared" in prompt.lower()


# --- Tests for validate_intent_declared fail-open behavior ---


def test_validate_intent_declaration_fails_open_on_empty_response():
    """Infrastructure failure (empty response) must fail-open: intent_found=True."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation", return_value=""
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is True


def test_validate_intent_declaration_fails_open_on_exception():
    """Exception from SDK must fail-open: intent_found=True."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation",
            side_effect=Exception("Connection refused"),
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is True


def test_validate_intent_declaration_blocks_on_explicit_no():
    """Explicit NO from LLM must block: intent_found=False."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation", return_value="NO"
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is False


def test_validate_intent_declaration_passes_on_yes():
    """YES from LLM must pass: intent_found=True."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation", return_value="YES"
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is True


def test_validate_intent_declaration_blocks_on_unexpected_response():
    """Unexpected non-empty response must block: intent_found=False."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation",
            return_value="MAYBE",
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is False


def test_stop_hook_prompt_contains_e2e_enforcement_for_story_epic():
    """Stop hook prompt must enforce E2E testing for story/epic implementations."""
    from pacemaker.intent_validator import get_prompt_template

    template = get_prompt_template()

    # Must mention story/epic detection
    assert "story" in template.lower() or "epic" in template.lower()
    # Must mention E2E testing requirement
    assert "e2e" in template.lower() or "end-to-end" in template.lower()
    # Must block when missing
    assert "BLOCKED" in template


def test_stop_hook_prompt_requires_e2e_declaration_for_story_epic():
    """Stop hook prompt must demand declaration of E2E approach for story/epic work."""
    from pacemaker.intent_validator import get_prompt_template

    template = get_prompt_template()

    # Must mention manual-test-executor or equivalent real-world execution method
    assert (
        "manual-test-executor" in template
        or "execute-e2e" in template
        or "end-to-end" in template.lower()
    )


def test_stop_hook_prompt_excludes_coded_tests_as_e2e_evidence():
    """Stop hook prompt must explicitly state coded tests (pytest/unit tests) do NOT
    satisfy E2E validation, and must require real application execution with no mocks.
    """
    import re
    from pacemaker.intent_validator import get_prompt_template

    template = get_prompt_template()

    # Pytest/unit tests must be explicitly negated — negation must appear near "pytest"
    assert re.search(
        r"(not|does not|do not|NOT)\b.{0,80}\bpytest\b",
        template,
        re.IGNORECASE | re.DOTALL,
    ), "Prompt must explicitly state pytest does NOT satisfy E2E requirement"

    # Must prohibit mocks with explicit NO language
    assert re.search(
        r"\bNO\s+mocks?\b",
        template,
    ), "Prompt must contain 'NO mocks' to prohibit mock-based validation"

    # Must require executing the real application (not test code)
    assert re.search(
        r"actually\s+(EXECUTE|run|invoke)\b.{0,60}\b(application|feature|system)\b",
        template,
        re.IGNORECASE | re.DOTALL,
    ), "Prompt must require actually executing the real application/system"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Test exclusions list mirrors the real defaults used by load_exclusions()
_TEST_EXCLUSIONS = [
    ".tmp/",
    "test/",
    "tests/",
    "fixtures/",
    "__pycache__/",
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    ".git/",
]


def _check(message: str, file_path: str, exclusions=None) -> str:
    """Call _regex_stage1_check with default test exclusions."""
    from pacemaker.intent_validator import _regex_stage1_check

    return _regex_stage1_check(
        message, file_path, _TEST_EXCLUSIONS if exclusions is None else exclusions
    )


# ---------------------------------------------------------------------------
# AC1: Intent marker positive (7 cases)
# ---------------------------------------------------------------------------


def test_regex_ac1_intent_marker_uppercase():
    """INTENT: uppercase is detected."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar(). Test: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac1_intent_marker_lowercase():
    """intent: lowercase is detected."""
    result = _check(
        "intent: Modify src/foo.py to add bar(). Test: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac1_intent_marker_mixed_case():
    """Intent: mixed case is detected."""
    result = _check(
        "Intent: Modify src/foo.py to add bar(). Test: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac1_intent_marker_space_before_colon():
    """INTENT : with space before colon is detected."""
    result = _check(
        "INTENT : Modify src/foo.py to add bar(). Test: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac1_intent_marker_mid_message():
    """INTENT: appearing in the middle of a longer message is detected."""
    msg = (
        "I will now proceed.\n\n"
        "INTENT: Modify src/foo.py to add bar(). Test: tests/test_foo.py::test_bar\n\n"
        "Then use Write tool."
    )
    result = _check(msg, "src/foo.py")
    assert result == "YES"


def test_regex_ac1_intent_marker_between_paragraphs():
    """INTENT: between paragraphs is detected."""
    msg = (
        "First paragraph.\n\n"
        "INTENT: Modify src/foo.py. Test: tests/test_foo.py::test_bar\n\n"
        "Second paragraph."
    )
    result = _check(msg, "src/foo.py")
    assert result == "YES"


def test_regex_ac1_intent_marker_after_newline():
    """INTENT: immediately after newline is detected."""
    msg = "Some text\nINTENT: Modify src/foo.py. Test: tests/test_foo.py::test_bar"
    result = _check(msg, "src/foo.py")
    assert result == "YES"


# ---------------------------------------------------------------------------
# AC2: Intent marker negative (9 cases)
# ---------------------------------------------------------------------------


def test_regex_ac2_no_marker_returns_no():
    """No INTENT: marker at all returns NO."""
    result = _check("I am going to modify src/foo.py to add bar.", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_describes_intent_without_marker():
    """Describing intent without INTENT: marker returns NO."""
    result = _check("My intention is to modify src/foo.py", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_vague_description_returns_no():
    """Vague description without marker returns NO."""
    result = _check("Fixing the thing in src/foo.py because reasons.", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_no_specifics_returns_no():
    """Message with no specifics and no marker returns NO."""
    result = _check("Making changes to src/foo.py", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_word_without_colon_returns_no():
    """Word 'INTENT' without colon returns NO."""
    result = _check("INTENT Modify src/foo.py to add bar", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_missing_colon_returns_no():
    """'intent' without colon returns NO."""
    result = _check("intent modify src/foo.py", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_conversational_returns_no():
    """Conversational message without INTENT: marker returns NO."""
    result = _check("Sure, I'll help you with src/foo.py", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_empty_message_returns_no():
    """Empty message returns NO."""
    result = _check("", "src/foo.py")
    assert result == "NO"


def test_regex_ac2_substring_unintentional_returns_no():
    """'unintentional' substring does not trigger intent marker."""
    result = _check("This was unintentional change to src/foo.py", "src/foo.py")
    assert result == "NO"


# ---------------------------------------------------------------------------
# AC3: File matching positive (6 cases)
# ---------------------------------------------------------------------------


def test_regex_ac3_file_basename_match():
    """File basename appears in message → match."""
    result = _check(
        "INTENT: Modify foo.py to add bar(). Test: tests/test_foo.py::t",
        "scripts/foo.py",
    )
    assert result == "YES"


def test_regex_ac3_full_path_match():
    """Full file path appears in message → match."""
    result = _check(
        "INTENT: Modify scripts/utils/foo.py to add bar(). Test: tests/t::t",
        "scripts/utils/foo.py",
    )
    assert result == "YES"


def test_regex_ac3_file_before_intent_marker():
    """File path before INTENT: marker is detected (CHECK 2 searches full message)."""
    result = _check(
        "Working on foo.py now.\n\nINTENT: Add bar(). Test: tests/t::t",
        "scripts/foo.py",
    )
    assert result == "YES"


def test_regex_ac3_filename_with_underscore():
    """Filename with underscore matches."""
    result = _check(
        "INTENT: Modify my_module.py to add helper(). Test: tests/t::t",
        "scripts/my_module.py",
    )
    assert result == "YES"


def test_regex_ac3_dunder_filename():
    """Dunder filename like __init__.py matches."""
    result = _check(
        "INTENT: Modify __init__.py to export new symbol. Test: tests/t::t",
        "scripts/__init__.py",
    )
    assert result == "YES"


def test_regex_ac3_deeply_nested_path():
    """Deeply nested path: basename still matches."""
    result = _check(
        "INTENT: Modify deep.py to add func(). Test: tests/t::t",
        "a/b/c/d/deep.py",
    )
    assert result == "YES"


# ---------------------------------------------------------------------------
# AC4: File matching negative (7 cases)
# ---------------------------------------------------------------------------


def test_regex_ac4_similar_name_no_match():
    """Similar but different filename returns NO."""
    result = _check(
        "INTENT: Modify foobar.py to add func(). Test: tests/t::t",
        "scripts/foo.py",
    )
    assert result == "NO"


def test_regex_ac4_semantic_reference_no_match():
    """Semantic reference ('the file') without actual filename returns NO."""
    result = _check(
        "INTENT: Modify the file to add func(). Test: tests/t::t",
        "src/foo.py",
    )
    assert result == "NO"


def test_regex_ac4_partial_name_no_match():
    """Partial filename ('foo' instead of 'foo.py') returns NO."""
    result = _check(
        "INTENT: Modify foo to add func(). Test: tests/t::t",
        "src/foo.py",
    )
    assert result == "NO"


def test_regex_ac4_case_mismatch_no_match():
    """Case mismatch ('FOO.py' instead of 'foo.py') returns NO."""
    result = _check(
        "INTENT: Modify FOO.py to add func(). Test: tests/t::t",
        "src/foo.py",
    )
    assert result == "NO"


def test_regex_ac4_empty_message_after_marker_no_match():
    """INTENT: marker with no content and no filename returns NO."""
    result = _check("INTENT:", "src/foo.py")
    assert result == "NO"


def test_regex_ac4_substring_of_filename_no_match():
    """Substring 'foo' when filename is 'foobar.py' returns NO."""
    result = _check(
        "INTENT: Modify foo to fix bug. Test: tests/t::t",
        "src/foobar.py",
    )
    assert result == "NO"


def test_regex_ac4_empty_file_path_returns_no():
    """Empty file_path returns NO (precondition guard)."""
    result = _check("INTENT: Modify something.", "")
    assert result == "NO"


# ---------------------------------------------------------------------------
# AC5: Core path positive (8 cases)
# ---------------------------------------------------------------------------


def test_regex_ac5_src_relative():
    """src/ relative path → core path → NO_TDD without TDD declaration."""
    result = _check("INTENT: Modify src/foo.py to add bar().", "src/foo.py")
    assert result == "NO_TDD"


def test_regex_ac5_src_absolute():
    """Absolute /home/user/project/src/foo.py → core path → NO_TDD."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().",
        "/home/user/project/src/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac5_lib_relative():
    """lib/ relative path → core path → NO_TDD without TDD declaration."""
    result = _check("INTENT: Modify lib/bar.py to add baz().", "lib/bar.py")
    assert result == "NO_TDD"


def test_regex_ac5_lib_absolute():
    """Absolute /project/lib/bar.py → core path → NO_TDD."""
    result = _check(
        "INTENT: Modify lib/bar.py to add baz().",
        "/project/lib/bar.py",
    )
    assert result == "NO_TDD"


def test_regex_ac5_core_path():
    """core/ path → core path → NO_TDD."""
    result = _check("INTENT: Modify core/engine.py to fix bug.", "core/engine.py")
    assert result == "NO_TDD"


def test_regex_ac5_source_path():
    """source/ path → core path → NO_TDD."""
    result = _check(
        "INTENT: Modify source/module.py to add feature.", "source/module.py"
    )
    assert result == "NO_TDD"


def test_regex_ac5_libraries_path():
    """libraries/ path → core path → NO_TDD."""
    result = _check(
        "INTENT: Modify libraries/crypto.py to fix bug.", "libraries/crypto.py"
    )
    assert result == "NO_TDD"


def test_regex_ac5_kernel_path():
    """kernel/ path → core path → NO_TDD."""
    result = _check("INTENT: Modify kernel/sched.py to fix bug.", "kernel/sched.py")
    assert result == "NO_TDD"


def test_regex_ac5_code_relative():
    """code/ relative path → core path → NO_TDD without TDD declaration."""
    result = _check("INTENT: Modify code/foo.py to add bar().", "code/foo.py")
    assert result == "NO_TDD"


def test_regex_ac5_code_absolute():
    """Absolute /home/user/project/code/foo.py → core path → NO_TDD."""
    result = _check(
        "INTENT: Modify code/foo.py to add bar().",
        "/home/user/project/code/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac5_code_nested():
    """Nested code/ path (some/code/bar.py) → core path → NO_TDD."""
    result = _check(
        "INTENT: Modify bar.py to add baz().",
        "/some/code/bar.py",
    )
    assert result == "NO_TDD"


# ---------------------------------------------------------------------------
# AC6: Core path negative (non-core directories → YES when file is mentioned)
# ---------------------------------------------------------------------------


def test_regex_ac6_scripts_not_core():
    """scripts/ path is not a core path → YES."""
    result = _check("INTENT: Modify run.py to add flag.", "scripts/run.py")
    assert result == "YES"


def test_regex_ac6_tests_excluded():
    """tests/ is excluded → YES (excluded path bypass)."""
    result = _check("INTENT: Modify test_foo.py to add test.", "tests/test_foo.py")
    assert result == "YES"


def test_regex_ac6_config_not_core():
    """config/ path is not a core path → YES."""
    result = _check("INTENT: Modify config.yaml to add setting.", "config/config.yaml")
    assert result == "YES"


def test_regex_ac6_docs_not_core():
    """docs/ path is not a core path → YES."""
    result = _check("INTENT: Modify README.md to update docs.", "docs/README.md")
    assert result == "YES"


def test_regex_ac6_my_src_not_core():
    """my-src/ does not match src/ core path pattern → YES."""
    result = _check("INTENT: Modify my-src/foo.py to add bar().", "my-src/foo.py")
    assert result == "YES"


def test_regex_ac6_resources_not_core():
    """resources/ path is not a core path → YES."""
    result = _check(
        "INTENT: Modify resources/schema.json to update schema.",
        "resources/schema.json",
    )
    assert result == "YES"


def test_regex_ac6_github_workflows_not_core():
    """.github/workflows/ path is not a core path → YES."""
    result = _check("INTENT: Modify ci.yml to add job.", ".github/workflows/ci.yml")
    assert result == "YES"


# ---------------------------------------------------------------------------
# AC7: Excluded path bypass (9 cases)
# ---------------------------------------------------------------------------


def test_regex_ac7_tests_excluded():
    """tests/ path is excluded → YES (no TDD required)."""
    result = _check("INTENT: Modify test_bar.py to add test.", "tests/test_bar.py")
    assert result == "YES"


def test_regex_ac7_nested_tests_excluded():
    """Nested tests/ path is excluded → YES."""
    result = _check("INTENT: Modify test_bar.py to add test.", "src/tests/test_bar.py")
    assert result == "YES"


def test_regex_ac7_tmp_excluded():
    """.tmp/ path is excluded → YES."""
    result = _check("INTENT: Modify scratch.py for temp work.", ".tmp/scratch.py")
    assert result == "YES"


def test_regex_ac7_pycache_excluded():
    """__pycache__/ path is excluded → YES."""
    result = _check("INTENT: Modify cached.pyc.", "__pycache__/cached.pyc")
    assert result == "YES"


def test_regex_ac7_fixtures_excluded():
    """fixtures/ path is excluded → YES."""
    result = _check("INTENT: Modify fixture.json.", "fixtures/fixture.json")
    assert result == "YES"


def test_regex_ac7_node_modules_excluded():
    """node_modules/ path is excluded → YES."""
    result = _check(
        "INTENT: Modify package.js in node_modules.",
        "node_modules/lodash/package.js",
    )
    assert result == "YES"


def test_regex_ac7_vendor_excluded():
    """vendor/ path is excluded → YES."""
    result = _check("INTENT: Modify module.py in vendor.", "vendor/lib/module.py")
    assert result == "YES"


def test_regex_ac7_dist_excluded():
    """dist/ path is excluded → YES."""
    result = _check("INTENT: Modify dist/bundle.js.", "dist/bundle.js")
    assert result == "YES"


def test_regex_ac7_build_excluded():
    """build/ path is excluded → YES."""
    result = _check("INTENT: Modify build output.py artifact.", "build/output.py")
    assert result == "YES"


# ---------------------------------------------------------------------------
# AC8: TDD declaration positive (7 cases)
# ---------------------------------------------------------------------------


def test_regex_ac8_test_coverage_declaration():
    """'test coverage: tests/...' satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\nTest coverage: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac8_pytest_notation():
    """'test: tests/test_foo.py::test_bar' satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\nTest: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac8_tdd_lowercase():
    """'test coverage:' lowercase satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\ntest coverage: tests/test_foo.py",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac8_covered_by():
    """'Covered by tests/...' satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\nCovered by tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac8_covered_by_lowercase():
    """'covered by tests/...' lowercase satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\ncovered by tests/test_foo.py",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac8_test_colon_uppercase():
    """'Test: tests/...' satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py.\nTest: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac8_test_colon_lowercase():
    """'test: tests/...' lowercase satisfies TDD requirement."""
    result = _check(
        "INTENT: Modify src/foo.py.\ntest: tests/test_foo.py::test_bar",
        "src/foo.py",
    )
    assert result == "YES"


# ---------------------------------------------------------------------------
# AC9: TDD declaration negative (4 cases)
# ---------------------------------------------------------------------------


def test_regex_ac9_promise_later_no_tdd():
    """'I will write tests later' does NOT satisfy TDD — returns NO_TDD."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar(). I will write tests later.",
        "src/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac9_empty_after_test_colon_no_tdd():
    """'test:' with nothing after colon does NOT satisfy TDD."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\ntest:",
        "src/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac9_conversational_no_tdd():
    """Conversational mention of tests without structured declaration → NO_TDD."""
    result = _check(
        "INTENT: Modify src/foo.py. The tests should be fine.",
        "src/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac9_no_test_text_no_tdd():
    """No test-related text at all in core path → NO_TDD."""
    result = _check("INTENT: Modify src/foo.py to refactor internals.", "src/foo.py")
    assert result == "NO_TDD"


# ---------------------------------------------------------------------------
# AC10: TDD skip positive (3 cases)
# ---------------------------------------------------------------------------


def test_regex_ac10_user_permission_with_to():
    """'User permission to skip TDD' satisfies skip requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\nUser permission to skip TDD: User said 'skip tests'.",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac10_user_permission_lowercase():
    """'user permission to skip tdd' lowercase satisfies skip requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\nuser permission to skip tdd: User said skip.",
        "src/foo.py",
    )
    assert result == "YES"


def test_regex_ac10_user_permission_without_to():
    """'User permission skip TDD' without 'to' satisfies skip requirement."""
    result = _check(
        "INTENT: Modify src/foo.py to add bar().\nUser permission skip TDD: approved.",
        "src/foo.py",
    )
    assert result == "YES"


# ---------------------------------------------------------------------------
# AC11: TDD skip negative (3 cases)
# ---------------------------------------------------------------------------


def test_regex_ac11_conversational_skip_mention_no_effect():
    """'The user wants to skip' does NOT satisfy skip requirement → NO_TDD."""
    result = _check(
        "INTENT: Modify src/foo.py. The user wants to skip testing.",
        "src/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac11_missing_keywords_no_effect():
    """'permission granted' without required keywords → NO_TDD."""
    result = _check(
        "INTENT: Modify src/foo.py. Permission granted by user.",
        "src/foo.py",
    )
    assert result == "NO_TDD"


def test_regex_ac11_wrong_keyword_no_effect():
    """'User permission to ignore tests' (wrong keyword) → NO_TDD."""
    result = _check(
        "INTENT: Modify src/foo.py. User permission to ignore tests.",
        "src/foo.py",
    )
    assert result == "NO_TDD"


# ---------------------------------------------------------------------------
# AC12: Version bump bypass (9 cases)
# ---------------------------------------------------------------------------


def test_regex_ac12_bump_to_with_digit():
    """'bump the version to 2.0.0' → YES (no TDD required)."""
    result = _check(
        "INTENT: Modify src/__init__.py to bump the version to 2.0.0.",
        "src/__init__.py",
    )
    assert result == "YES"


def test_regex_ac12_update_from_to_with_digit():
    """'update the version from 1.0 to 2.0' → YES."""
    result = _check(
        "INTENT: Modify src/__init__.py to update the version from 1.0 to 2.0.",
        "src/__init__.py",
    )
    assert result == "YES"


def test_regex_ac12_version_bump_to_with_digit():
    """'version bump to 3.1.4' → YES."""
    result = _check(
        "INTENT: Modify src/__init__.py - version bump to 3.1.4.",
        "src/__init__.py",
    )
    assert result == "YES"


def test_regex_ac12_set_version_with_digit():
    """'set version to 5.0' → YES."""
    result = _check(
        "INTENT: Modify src/__init__.py to set version to 5.0.",
        "src/__init__.py",
    )
    assert result == "YES"


def test_regex_ac12_change_version_with_digit():
    """'change the version 2.1' → YES."""
    result = _check(
        "INTENT: Modify src/__init__.py to change the version 2.1.",
        "src/__init__.py",
    )
    assert result == "YES"


def test_regex_ac12_no_digit_feature_not_bypass():
    """'bump version' without a digit does NOT bypass → NO_TDD."""
    result = _check(
        "INTENT: Modify src/__init__.py to bump version for new feature.",
        "src/__init__.py",
    )
    assert result == "NO_TDD"


def test_regex_ac12_no_digit_validation_not_bypass():
    """'update the version' without digit does NOT bypass → NO_TDD."""
    result = _check(
        "INTENT: Modify src/__init__.py to update the version for validation.",
        "src/__init__.py",
    )
    assert result == "NO_TDD"


def test_regex_ac12_no_digit_logic_not_bypass():
    """'change version' without digit does NOT bypass → NO_TDD."""
    result = _check(
        "INTENT: Modify src/__init__.py to change version logic.",
        "src/__init__.py",
    )
    assert result == "NO_TDD"


def test_regex_ac12_bare_bump_no_digit_not_bypass():
    """'bump the version' without any digit → NO_TDD."""
    result = _check(
        "INTENT: Modify src/__init__.py to bump the version string.",
        "src/__init__.py",
    )
    assert result == "NO_TDD"


# ---------------------------------------------------------------------------
# AC17: Absolute paths (2 cases)
# ---------------------------------------------------------------------------


def test_regex_ac17_src_absolute_with_tdd():
    """Absolute src path with TDD declaration → YES."""
    result = _check(
        "INTENT: Modify src/auth.py to add validate().\nTest: tests/test_auth.py::test_validate",
        "/home/user/project/src/auth.py",
    )
    assert result == "YES"


def test_regex_ac17_lib_absolute_without_tdd():
    """Absolute lib path without TDD declaration → NO_TDD."""
    result = _check(
        "INTENT: Modify lib/utils.py to refactor.",
        "/opt/project/lib/utils.py",
    )
    assert result == "NO_TDD"


# ---------------------------------------------------------------------------
# AC18: Empty/invalid file_path (2 cases)
# ---------------------------------------------------------------------------


def test_regex_ac18_empty_string_returns_no():
    """Empty string file_path returns NO (precondition guard)."""
    result = _check("INTENT: Modify something.", "")
    assert result == "NO"


def test_regex_ac18_none_returns_no():
    """None file_path returns NO (precondition guard)."""
    from pacemaker.intent_validator import _regex_stage1_check

    result = _regex_stage1_check("INTENT: Modify something.", None, _TEST_EXCLUSIONS)
    assert result == "NO"


# ---------------------------------------------------------------------------
# Unit tests: Stage 1 gating in validate_intent_and_code()
# These test Stage 1 behavior in isolation using hook_model="gpt-5" to bypass
# the SDK_AVAILABLE check. Stage 2 is patched since it calls external services.
# ---------------------------------------------------------------------------


def test_stage1_gate_no_marker_blocks_before_stage2():
    """Stage 1 NO (no INTENT marker) blocks without calling Stage 2."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch("pacemaker.intent_validator._call_stage2_validation") as mock_stage2:
        result = validate_intent_and_code(
            messages=["No intent marker here."],
            code="def bar(): pass",
            file_path="scripts/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is False
    assert "intent" in result["feedback"].lower()
    mock_stage2.assert_not_called()


def test_stage1_gate_no_tdd_blocks_before_stage2():
    """Stage 1 NO_TDD (core path, missing TDD) blocks without calling Stage 2."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch("pacemaker.intent_validator._call_stage2_validation") as mock_stage2:
        result = validate_intent_and_code(
            messages=["INTENT: Modify src/foo.py to add bar()."],
            code="def bar(): pass",
            file_path="src/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is False
    assert result.get("tdd_failure") is True
    mock_stage2.assert_not_called()


def test_stage1_gate_yes_calls_stage2():
    """Stage 1 YES (valid intent + TDD) proceeds to Stage 2."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch(
        "pacemaker.intent_validator._call_stage2_validation", return_value="APPROVED"
    ) as mock_stage2:
        result = validate_intent_and_code(
            messages=[
                "INTENT: Modify scripts/foo.py to add bar().\n"
                "Test: tests/test_foo.py::test_bar"
            ],
            code="def bar(): pass",
            file_path="scripts/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is True
    mock_stage2.assert_called_once()


def test_regex_tdd_word_boundary_prevents_latest():
    """'laTEST:' should not be detected as TDD declaration."""
    result = _check(
        "INTENT: Modify src/auth.py to add the laTEST: version of the code.\nsrc/auth.py",
        "src/auth.py",
    )
    assert result == "NO_TDD"


def test_regex_ac12_version_bump_across_newline():
    """Version bump with newline before digit should still bypass TDD requirement."""
    result = _check(
        "INTENT: Modify __init__.py to bump version to\n2.12.0\n__init__.py",
        "src/pacemaker/__init__.py",
    )
    assert result == "YES"


# ---------------------------------------------------------------------------
# Tests: validate_intent_and_code() includes "reviewer" field in result dict
# ---------------------------------------------------------------------------


def test_validate_intent_and_code_stage1_no_includes_reviewer():
    """Stage 1 NO (no INTENT marker) result includes reviewer field."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch("pacemaker.intent_validator._call_stage2_validation"):
        result = validate_intent_and_code(
            messages=["No intent marker here."],
            code="def bar(): pass",
            file_path="scripts/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is False
    assert "reviewer" in result


def test_validate_intent_and_code_stage1_no_tdd_includes_reviewer():
    """Stage 1 NO_TDD (core path, missing TDD) result includes reviewer field."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch("pacemaker.intent_validator._call_stage2_validation"):
        result = validate_intent_and_code(
            messages=["INTENT: Modify src/foo.py to add bar()."],
            code="def bar(): pass",
            file_path="src/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is False
    assert result.get("tdd_failure") is True
    assert "reviewer" in result


def test_validate_intent_and_code_stage2_approved_includes_reviewer():
    """Stage 2 APPROVED result includes reviewer field set to provider name."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch(
        "pacemaker.inference.registry.resolve_and_call_with_reviewer",
        return_value=("APPROVED", "codex-gpt5"),
    ):
        result = validate_intent_and_code(
            messages=[
                "INTENT: Modify scripts/foo.py to add bar().\n"
                "Test: tests/test_foo.py::test_bar"
            ],
            code="def bar(): pass",
            file_path="scripts/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is True
    assert "reviewer" in result
    assert result["reviewer"] == "codex-gpt5"


def test_validate_intent_and_code_stage2_blocked_includes_reviewer():
    """Stage 2 BLOCKED result includes reviewer field set to provider name."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_and_code

    with patch(
        "pacemaker.inference.registry.resolve_and_call_with_reviewer",
        return_value=("Code review feedback here.", "anthropic-sdk"),
    ):
        result = validate_intent_and_code(
            messages=[
                "INTENT: Modify scripts/foo.py to add bar().\n"
                "Test: tests/test_foo.py::test_bar"
            ],
            code="def bar(): pass",
            file_path="scripts/foo.py",
            tool_name="Write",
            hook_model="gpt-5",
        )

    assert result["approved"] is False
    assert "reviewer" in result
    assert result["reviewer"] == "anthropic-sdk"
