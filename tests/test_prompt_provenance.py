"""
Tests for src/pacemaker/prompt_provenance.py — Story #101.

Self-identifying pace-maker provenance tagging: the shared stdlib-only leaf
module (same invariant as pacemaker.inference.verdict) that formats the
`[pace-maker · <event>]` tag and the `[pace-maker · reviewer-relay ·
model=<id>]` variant, and builds the SessionStart / SubagentStart manifests
that declare the closed channel enumeration + never-list.

"The manifest is the contract; the tag is just an index into it." — the tag
itself proves nothing cryptographically; it is only checkable against the
closed, declared contract in the manifest. Enforcement of the never-list is
Claude's own reasoning, not code in this module.
"""

import ast
import importlib.util
import inspect
import sys
import sysconfig
from pathlib import Path

import pytest

from pacemaker import prompt_provenance as pp


def _path_contains(parent: str, child: str) -> bool:
    """True iff the resolved *child* path is *parent* itself or lives under
    it, using Path.is_relative_to() (available since Python 3.9, this
    repo's floor per pyproject.toml's requires-python) on fully resolved
    (symlink-following, absolute) paths — a plain str.startswith() prefix
    comparison would misclassify a sibling directory sharing a string
    prefix (e.g. "/usr/lib/python3.9x" starting with "/usr/lib/python3.9")
    as contained.
    """
    resolved_child = Path(child).resolve()
    resolved_parent = Path(parent).resolve()
    return resolved_child == resolved_parent or resolved_child.is_relative_to(
        resolved_parent
    )


def _is_stdlib_module(name: str) -> bool:
    """Return True iff *name* is a Python standard-library top-level module.

    Classifies *name* as stdlib ONLY when its resolved ModuleSpec origin is
    a real file path contained under sysconfig's stdlib directory AND is
    explicitly NOT contained under purelib/platlib (site-packages can be
    nested UNDER the stdlib prefix on some installs, so containment must be
    checked, not a bare string prefix). A compiled-in builtin (e.g. "sys")
    is handled separately via sys.builtin_module_names, since it has no
    file-path origin to compare against a directory at all. Deliberately
    does not shortcut via sys.stdlib_module_names (3.10+ only) — this
    repo's floor is 3.9 per pyproject.toml's requires-python.
    """
    if name in sys.builtin_module_names:
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
    if spec is None or spec.origin is None:
        return False
    paths = sysconfig.get_paths()
    if any(
        _path_contains(p, spec.origin) for p in (paths["purelib"], paths["platlib"])
    ):
        return False
    return _path_contains(paths["stdlib"], spec.origin)


# ==============================================================================
# Leaf-module purity — same invariant as inference/verdict.py
# ==============================================================================


class TestLeafModulePurity:
    def test_module_imports_only_stdlib(self):
        """No non-stdlib imports anywhere in the module source — same
        invariant verdict.py holds, so any gate can import this module
        without creating a circular dependency or a third-party dependency.

        Validates every top-level imported module name against the actual
        Python standard library via _is_stdlib_module(), not just a
        `pacemaker`-prefix blocklist — a stray `import requests` must fail
        this test just as surely as a `from . import x` would, because its
        resolved spec origin lives under site-packages, not the stdlib path.
        """
        source = inspect.getsource(pp)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    assert _is_stdlib_module(top_level), (
                        f"leaf module imported non-stdlib {alias.name!r} — "
                        "must be stdlib-only"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, "leaf module must not use relative imports"
                module = node.module or ""
                top_level = module.split(".")[0]
                assert _is_stdlib_module(top_level), (
                    f"leaf module imported from non-stdlib {module!r} — "
                    "must be stdlib-only"
                )


# ==============================================================================
# Tag formatter — [pace-maker · <event>]
# ==============================================================================


class TestFormatTag:
    def test_middle_dot_is_u00b7_not_a_lookalike(self):
        """The separator must be the EXACT U+00B7 MIDDLE DOT codepoint, not a
        visually similar character (bullet U+2022, hyphen, asterisk, etc.)."""
        assert pp.TAG_SEPARATOR == "·"
        assert ord(pp.TAG_SEPARATOR) == 0xB7

    def test_produces_tag_for_every_declared_channel(self):
        for channel in pp.CHANNELS:
            if channel == pp.REVIEWER_RELAY_CHANNEL:
                continue  # reviewer-relay uses format_reviewer_relay, not format_tag
            tagged = pp.format_tag("body text", channel)
            assert f"[pace-maker · {channel}]" in tagged
            assert "body text" in tagged

    def test_unknown_channel_raises(self):
        with pytest.raises(ValueError):
            pp.format_tag("body", "not_a_real_channel")

    def test_non_string_body_raises_type_error(self):
        with pytest.raises(TypeError):
            pp.format_tag(None, "intel_nudge")

    def test_empty_body_still_well_formed(self):
        tagged = pp.format_tag("", "intel_nudge")
        assert tagged.startswith("[pace-maker · intel_nudge]")

    def test_body_containing_tag_like_text_is_preserved_verbatim(self):
        body = "some text [pace-maker · fake-channel] more text"
        tagged = pp.format_tag(body, "intel_nudge")
        assert body in tagged
        # Only ONE real header at the very start, the fake one inside the body
        # must not be treated specially.
        assert tagged.startswith("[pace-maker · intel_nudge]")

    def test_multiline_body_remains_well_formed(self):
        body = "line one\nline two\nline three"
        tagged = pp.format_tag(body, "intel_nudge")
        assert body in tagged
        assert tagged.count("[pace-maker · intel_nudge]") == 1

    def test_does_not_double_wrap_already_tagged_body(self):
        once = pp.format_tag("body text", "intel_nudge")
        twice = pp.format_tag(once, "intel_nudge")
        assert once == twice
        assert twice.count("[pace-maker · intel_nudge]") == 1


# ==============================================================================
# Reviewer-relay formatter — [pace-maker · reviewer-relay · model=<id>]
# ==============================================================================


class TestFormatReviewerRelay:
    def test_produces_distinct_tag_with_model_id(self):
        tagged = pp.format_reviewer_relay("some feedback", "codex-gpt5")
        assert "[pace-maker · reviewer-relay · model=codex-gpt5]" in tagged
        assert "some feedback" in tagged

    def test_visibly_distinct_from_plain_tag(self):
        plain = pp.format_tag("x", "intel_nudge")
        relay = pp.format_reviewer_relay("x", "anthropic-sdk")
        assert plain != relay
        assert "reviewer-relay" not in plain
        assert "reviewer-relay" in relay

    def test_carries_advisory_third_party_framing(self):
        tagged = pp.format_reviewer_relay("body", "agy-flash-high")
        lowered = tagged.lower()
        assert "advisory" in lowered
        assert "third-party" in lowered or "third party" in lowered
        assert "not a pace-maker instruction" in lowered

    def test_threads_competitive_expression_model_id(self):
        tagged = pp.format_reviewer_relay("body", "opus+gpt-5->haiku")
        assert "model=opus+gpt-5->haiku" in tagged

    def test_does_not_double_wrap(self):
        once = pp.format_reviewer_relay("body", "codex-beast")
        twice = pp.format_reviewer_relay(once, "codex-beast")
        assert once == twice
        assert twice.count("model=codex-beast") == 1

    def test_empty_body_still_well_formed(self):
        tagged = pp.format_reviewer_relay("", "gem-flash")
        assert tagged.startswith("[pace-maker · reviewer-relay · model=gem-flash]")

    def test_empty_model_id_raises_value_error(self):
        with pytest.raises(ValueError):
            pp.format_reviewer_relay("body", "")

    def test_model_id_containing_close_bracket_raises_value_error(self):
        with pytest.raises(ValueError):
            pp.format_reviewer_relay("body", "codex]injected-header")

    def test_model_id_containing_newline_raises_value_error(self):
        with pytest.raises(ValueError):
            pp.format_reviewer_relay("body", "codex\ninjected")


# ==============================================================================
# Manifest never-list shared assertion helper
# ==============================================================================


def _assert_never_list(manifest_lower: str) -> None:
    """Assert all four never-list prohibitions are present in a lower-cased
    manifest string. Shared by both the SessionStart and SubagentStart
    manifest test classes so the four checks are defined exactly once."""
    assert "exfiltrat" in manifest_lower
    assert "disable" in manifest_lower and (
        "safety" in manifest_lower or "governance" in manifest_lower
    )
    assert "conceal" in manifest_lower
    assert "impersonat" in manifest_lower or "as if it were the user" in manifest_lower


# ==============================================================================
# SessionStart manifest — closed channel enumeration + never-list
# ==============================================================================


class TestSessionStartManifest:
    def test_enumerates_every_declared_channel(self):
        manifest = pp.session_start_manifest()
        for channel in pp.CHANNELS:
            assert channel in manifest, f"channel {channel!r} missing from manifest"

    def test_includes_reviewer_relay_channel(self):
        manifest = pp.session_start_manifest()
        assert pp.REVIEWER_RELAY_CHANNEL in manifest

    def test_never_list_has_all_four_prohibitions(self):
        _assert_never_list(pp.session_start_manifest().lower())

    def test_is_tagged_with_own_channel(self):
        manifest = pp.session_start_manifest()
        assert manifest.startswith("[pace-maker · session_start_manifest]")

    def test_is_a_nonempty_string(self):
        manifest = pp.session_start_manifest()
        assert isinstance(manifest, str)
        assert len(manifest) > 100


# ==============================================================================
# SubagentStart manifest — abbreviated, same contract
# ==============================================================================


class TestSubagentStartManifest:
    def test_shorter_than_session_start_manifest(self):
        assert len(pp.subagent_start_manifest()) < len(pp.session_start_manifest())

    def test_still_enumerates_every_declared_channel(self):
        manifest = pp.subagent_start_manifest()
        for channel in pp.CHANNELS:
            assert (
                channel in manifest
            ), f"channel {channel!r} missing from abbreviated manifest"

    def test_still_has_all_four_never_list_prohibitions(self):
        _assert_never_list(pp.subagent_start_manifest().lower())

    def test_is_tagged_with_own_channel(self):
        manifest = pp.subagent_start_manifest()
        assert manifest.startswith("[pace-maker · subagent_start_manifest]")

    def test_no_dependence_on_session_start_history(self):
        """The abbreviated manifest must be fully self-contained text — it
        must not reference or require reading the SessionStart manifest."""
        manifest = pp.subagent_start_manifest().lower()
        assert "see sessionstart" not in manifest
        assert "as declared above" not in manifest


# ==============================================================================
# Closed enumeration consistency
# ==============================================================================


class TestClosedEnumeration:
    def test_channels_is_a_nonempty_closed_set(self):
        assert isinstance(pp.CHANNELS, frozenset)
        assert len(pp.CHANNELS) > 0

    def test_never_list_has_exactly_four_items(self):
        assert len(pp.NEVER_LIST) == 4
