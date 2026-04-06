#!/usr/bin/env python3
"""
Tests for danger_bash_rules CRUD operations.

Covers:
- _validate_rule_id: empty, whitespace-only, internal whitespace
- _write_config: directory creation, atomic write, temp cleanup on failure, overwrite
- add_rule: valid custom, invalid regex, invalid category, default ID rejected,
            duplicate ID rejected, empty ID, whitespace ID, internal whitespace ID
- restore_rule: restore deleted, not-a-default error, not-deleted error, active after restore
- remove_rule: remove default (suppression), remove custom, already-deleted error, not-found
- modify_rule: modify description, modify category, invalid category, default rejected,
               pattern field rejected, not-found, id field in updates silently ignored
- format_rules_for_display: source tags, pattern truncated with "..." at MAX_PATTERN_DISPLAY_LEN,
                             sort order, no tags without config_path, empty rules message
- load_rules same-ID warning
- Module-level constants: VALID_CATEGORIES, MAX_PATTERN_DISPLAY_LEN
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pacemaker.danger_bash_rules import (
    VALID_CATEGORIES,
    MAX_PATTERN_DISPLAY_LEN,
    _load_custom_config,
    _validate_rule_id,
    _write_config,
    add_rule,
    format_rules_for_display,
    load_default_rules,
    load_rules,
    modify_rule,
    remove_rule,
    restore_rule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path, rules=None, deleted_rules=None):
    """Write a YAML config file and return its path."""
    path = os.path.join(str(tmp_path), "danger_bash_rules.yaml")
    data = {
        "rules": rules or [],
        "deleted_rules": deleted_rules or [],
    }
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    return path


def _first_default_id():
    """Return the ID of the first default rule."""
    return load_default_rules()[0]["id"]


def _nonexistent_path(tmp_path):
    """Return a path that does not yet exist."""
    return os.path.join(str(tmp_path), "subdir", "rules.yaml")


# ---------------------------------------------------------------------------
# TestValidateRuleId
# ---------------------------------------------------------------------------


class TestValidateRuleId:
    """Tests for _validate_rule_id() — required for 100% coverage of the helper."""

    def test_valid_id_does_not_raise(self):
        _validate_rule_id("MY-CUSTOM-001")  # must not raise

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_rule_id("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_rule_id("   ")

    def test_id_with_internal_whitespace_raises(self):
        with pytest.raises(ValueError, match="whitespace"):
            _validate_rule_id("MY RULE")

    def test_id_with_tab_raises(self):
        with pytest.raises(ValueError, match="whitespace"):
            _validate_rule_id("MY\tRULE")

    def test_id_with_newline_raises(self):
        with pytest.raises(ValueError, match="whitespace"):
            _validate_rule_id("MY\nRULE")


# ---------------------------------------------------------------------------
# TestWriteConfig
# ---------------------------------------------------------------------------


class TestWriteConfig:
    """Tests for _write_config().

    Temp file naming contract: _write_config uses `config_path + ".tmp"` as the
    intermediate file, matching the clean_code_rules._write_config() pattern.
    """

    def test_creates_parent_directory(self, tmp_path):
        path = os.path.join(str(tmp_path), "newdir", "rules.yaml")
        config = {"rules": [], "deleted_rules": []}
        _write_config(path, config)
        assert os.path.exists(path)

    def test_writes_valid_yaml(self, tmp_path):
        path = os.path.join(str(tmp_path), "rules.yaml")
        config = {
            "rules": [
                {
                    "id": "MY-001",
                    "pattern": r"rm\s",
                    "category": "work_destruction",
                    "description": "test",
                }
            ],
            "deleted_rules": ["WD-001"],
        }
        _write_config(path, config)
        with open(path) as f:
            loaded = yaml.safe_load(f)
        assert loaded["deleted_rules"] == ["WD-001"]
        assert loaded["rules"][0]["id"] == "MY-001"

    def test_atomic_write_no_temp_file_left(self, tmp_path):
        path = os.path.join(str(tmp_path), "rules.yaml")
        config = {"rules": [], "deleted_rules": []}
        _write_config(path, config)
        assert not os.path.exists(path + ".tmp")

    def test_temp_file_cleaned_up_on_failure(self, tmp_path, monkeypatch):
        """If os.replace fails, the .tmp file (path + '.tmp') must be cleaned up."""
        path = os.path.join(str(tmp_path), "rules.yaml")
        config = {"rules": [], "deleted_rules": []}

        def bad_replace(src, dst):
            raise OSError("simulated failure")

        monkeypatch.setattr(os, "replace", bad_replace)
        with pytest.raises(OSError):
            _write_config(path, config)
        assert not os.path.exists(path + ".tmp")

    def test_overwrites_existing_file(self, tmp_path):
        path = _make_config(tmp_path, deleted_rules=["WD-001"])
        config = {"rules": [], "deleted_rules": ["WD-002"]}
        _write_config(path, config)
        loaded = _load_custom_config(path)
        assert loaded["deleted_rules"] == ["WD-002"]


# ---------------------------------------------------------------------------
# TestAddRule
# ---------------------------------------------------------------------------


class TestAddRule:
    """Tests for add_rule()."""

    def _custom_rule(self, rule_id="MY-CUSTOM-001"):
        return {
            "id": rule_id,
            "pattern": r"my_danger_cmd\s",
            "category": "work_destruction",
            "description": "Test custom rule",
        }

    def test_add_valid_custom_rule(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule()
        add_rule(path, rule)
        loaded = _load_custom_config(path)
        assert any(r["id"] == "MY-CUSTOM-001" for r in loaded["rules"])

    def test_add_rule_persists_to_yaml(self, tmp_path):
        path = _make_config(tmp_path)
        add_rule(path, self._custom_rule())
        with open(path) as f:
            data = yaml.safe_load(f)
        assert any(r["id"] == "MY-CUSTOM-001" for r in data["rules"])

    def test_invalid_regex_raises_value_error(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule()
        rule["pattern"] = r"[invalid(regex"
        with pytest.raises(ValueError, match="[Pp]attern|regex"):
            add_rule(path, rule)

    def test_invalid_category_raises_value_error(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule()
        rule["category"] = "not_a_valid_category"
        with pytest.raises(ValueError, match="[Cc]ategory"):
            add_rule(path, rule)

    def test_default_id_rejected(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule(rule_id=_first_default_id())
        with pytest.raises(ValueError, match="default"):
            add_rule(path, rule)

    def test_duplicate_custom_id_rejected(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule()
        add_rule(path, rule)
        with pytest.raises(ValueError, match="[Dd]uplicate|already exists"):
            add_rule(path, self._custom_rule())

    def test_empty_id_raises(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule(rule_id="")
        with pytest.raises(ValueError):
            add_rule(path, rule)

    def test_whitespace_only_id_raises(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule(rule_id="   ")
        with pytest.raises(ValueError):
            add_rule(path, rule)

    def test_id_with_internal_whitespace_raises(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule(rule_id="MY RULE")
        with pytest.raises(ValueError, match="whitespace"):
            add_rule(path, rule)

    def test_nonexistent_config_creates_file(self, tmp_path):
        path = _nonexistent_path(tmp_path)
        add_rule(path, self._custom_rule())
        assert os.path.exists(path)

    def test_valid_system_destruction_category(self, tmp_path):
        path = _make_config(tmp_path)
        rule = self._custom_rule()
        rule["category"] = "system_destruction"
        add_rule(path, rule)  # must not raise
        loaded = _load_custom_config(path)
        assert any(r["id"] == "MY-CUSTOM-001" for r in loaded["rules"])


# ---------------------------------------------------------------------------
# TestRestoreRule
# ---------------------------------------------------------------------------


class TestRestoreRule:
    """Tests for restore_rule()."""

    def test_restore_deleted_default(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path, deleted_rules=[default_id])
        restore_rule(path, default_id)
        loaded = _load_custom_config(path)
        assert default_id not in loaded["deleted_rules"]

    def test_restore_writes_to_disk(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path, deleted_rules=[default_id])
        restore_rule(path, default_id)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert default_id not in (data.get("deleted_rules") or [])

    def test_not_a_default_raises(self, tmp_path):
        path = _make_config(tmp_path, deleted_rules=["WD-001"])
        with pytest.raises(ValueError, match="[Dd]efault|not a default"):
            restore_rule(path, "MY-CUSTOM-001")

    def test_not_deleted_raises(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path)  # nothing deleted
        with pytest.raises(ValueError, match="[Nn]ot deleted|not in deleted"):
            restore_rule(path, default_id)

    def test_restore_makes_rule_active_in_load_rules(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path, deleted_rules=[default_id])
        rules_before = load_rules(path)
        assert not any(r["id"] == default_id for r in rules_before)
        restore_rule(path, default_id)
        rules_after = load_rules(path)
        assert any(r["id"] == default_id for r in rules_after)


# ---------------------------------------------------------------------------
# TestRemoveRule
# ---------------------------------------------------------------------------


class TestRemoveRule:
    """Tests for remove_rule()."""

    def test_remove_default_adds_suppression(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path)
        remove_rule(path, default_id)
        loaded = _load_custom_config(path)
        assert default_id in loaded["deleted_rules"]

    def test_remove_default_suppresses_in_load_rules(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path)
        remove_rule(path, default_id)
        rules = load_rules(path)
        assert not any(r["id"] == default_id for r in rules)

    def test_remove_custom_rule(self, tmp_path):
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": "MY-CUSTOM-001",
                    "pattern": r"danger\s",
                    "category": "work_destruction",
                    "description": "d",
                }
            ],
        )
        remove_rule(path, "MY-CUSTOM-001")
        loaded = _load_custom_config(path)
        assert not any(r["id"] == "MY-CUSTOM-001" for r in loaded["rules"])
        # custom rule removal must NOT add a deleted_rules marker
        assert "MY-CUSTOM-001" not in loaded["deleted_rules"]

    def test_already_deleted_raises(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path, deleted_rules=[default_id])
        with pytest.raises(ValueError, match="[Aa]lready deleted"):
            remove_rule(path, default_id)

    def test_not_found_raises(self, tmp_path):
        path = _make_config(tmp_path)
        with pytest.raises(ValueError, match="[Nn]ot found"):
            remove_rule(path, "NONEXISTENT-999")

    def test_remove_writes_to_disk(self, tmp_path):
        default_id = _first_default_id()
        path = _make_config(tmp_path)
        remove_rule(path, default_id)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert default_id in (data.get("deleted_rules") or [])


# ---------------------------------------------------------------------------
# TestModifyRule
# ---------------------------------------------------------------------------


class TestModifyRule:
    """Tests for modify_rule().

    Spec: only 'description' and 'category' fields are allowed.
    Passing 'id' in updates is silently ignored (cannot change rule identity).
    Passing 'pattern' in updates raises ValueError (pattern field not allowed).
    Only custom rules can be modified; default rule IDs raise ValueError.
    """

    def _setup_custom(self, tmp_path, rule_id="MY-CUSTOM-001"):
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": rule_id,
                    "pattern": r"danger\s",
                    "category": "work_destruction",
                    "description": "Original description",
                }
            ],
        )
        return path

    def test_modify_description(self, tmp_path):
        path = self._setup_custom(tmp_path)
        modify_rule(path, "MY-CUSTOM-001", {"description": "New description"})
        loaded = _load_custom_config(path)
        rule = next(r for r in loaded["rules"] if r["id"] == "MY-CUSTOM-001")
        assert rule["description"] == "New description"

    def test_modify_category(self, tmp_path):
        path = self._setup_custom(tmp_path)
        modify_rule(path, "MY-CUSTOM-001", {"category": "system_destruction"})
        loaded = _load_custom_config(path)
        rule = next(r for r in loaded["rules"] if r["id"] == "MY-CUSTOM-001")
        assert rule["category"] == "system_destruction"

    def test_invalid_category_raises(self, tmp_path):
        path = self._setup_custom(tmp_path)
        with pytest.raises(ValueError, match="[Cc]ategory"):
            modify_rule(path, "MY-CUSTOM-001", {"category": "bad_category"})

    def test_modify_default_rule_rejected(self, tmp_path):
        """modify_rule must reject default rule IDs (only custom rules allowed)."""
        path = _make_config(tmp_path)
        with pytest.raises(ValueError, match="[Dd]efault|[Cc]ustom"):
            modify_rule(path, _first_default_id(), {"description": "hacked"})

    def test_modify_pattern_rejected(self, tmp_path):
        """Updating 'pattern' field must raise ValueError (not an allowed field)."""
        path = self._setup_custom(tmp_path)
        with pytest.raises(ValueError, match="[Pp]attern|[Ff]ield"):
            modify_rule(path, "MY-CUSTOM-001", {"pattern": r"new_pattern\s"})

    def test_not_found_raises(self, tmp_path):
        path = _make_config(tmp_path)
        with pytest.raises(ValueError, match="[Nn]ot found"):
            modify_rule(path, "NONEXISTENT-999", {"description": "x"})

    def test_modify_writes_to_disk(self, tmp_path):
        path = self._setup_custom(tmp_path)
        modify_rule(path, "MY-CUSTOM-001", {"description": "Written"})
        with open(path) as f:
            data = yaml.safe_load(f)
        rule = next(r for r in data["rules"] if r["id"] == "MY-CUSTOM-001")
        assert rule["description"] == "Written"

    def test_modify_id_field_silently_ignored(self, tmp_path):
        """Passing 'id' in updates is silently ignored — ID cannot be changed.

        Spec says only 'description' and 'category' are allowed fields;
        'id' in updates must not change rule identity.
        """
        path = self._setup_custom(tmp_path)
        modify_rule(path, "MY-CUSTOM-001", {"id": "CHANGED-ID", "description": "ok"})
        loaded = _load_custom_config(path)
        assert any(r["id"] == "MY-CUSTOM-001" for r in loaded["rules"])
        assert not any(r["id"] == "CHANGED-ID" for r in loaded["rules"])


# ---------------------------------------------------------------------------
# TestFormatRulesForDisplay
# ---------------------------------------------------------------------------


class TestFormatRulesForDisplay:
    """Tests for format_rules_for_display()."""

    def _runtime_rules(self, config_path):
        return load_rules(config_path)

    def test_default_rules_tagged_default(self, tmp_path):
        path = _make_config(tmp_path)
        rules = self._runtime_rules(path)
        output = format_rules_for_display(rules, config_path=path)
        assert "[default]" in output

    def test_custom_rule_tagged_custom(self, tmp_path):
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": "MY-CUSTOM-001",
                    "pattern": r"danger\s",
                    "category": "work_destruction",
                    "description": "Custom",
                }
            ],
        )
        rules = self._runtime_rules(path)
        output = format_rules_for_display(rules, config_path=path)
        assert "[custom]" in output

    def test_pattern_truncated_at_max_len(self, tmp_path):
        """Patterns longer than MAX_PATTERN_DISPLAY_LEN are truncated with '...' suffix."""
        long_pattern = "a" * (MAX_PATTERN_DISPLAY_LEN + 20)
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": "MY-CUSTOM-001",
                    "pattern": long_pattern,
                    "category": "work_destruction",
                    "description": "Long pattern rule",
                }
            ],
        )
        rules = self._runtime_rules(path)
        output = format_rules_for_display(rules, config_path=path)
        # Find the line that carries the (truncated) pattern value
        PATTERN_PREFIX_LEN = 10
        pattern_lines = [
            line
            for line in output.splitlines()
            if long_pattern[:PATTERN_PREFIX_LEN] in line
        ]
        assert pattern_lines, "Expected a line containing the (truncated) pattern"
        for line in pattern_lines:
            stripped = line.rstrip()
            assert (
                "..." in stripped
            ), f"Expected '...' in truncated pattern line: {stripped!r}"
            ellipsis_len = len("...")
            assert len(stripped) <= MAX_PATTERN_DISPLAY_LEN + ellipsis_len + len(
                "  Pattern: "
            )

    def test_stable_sort_by_id(self, tmp_path):
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": "ZZZ-001",
                    "pattern": r"z\s",
                    "category": "work_destruction",
                    "description": "Z",
                },
                {
                    "id": "AAA-001",
                    "pattern": r"a\s",
                    "category": "work_destruction",
                    "description": "A",
                },
            ],
        )
        rules = self._runtime_rules(path)
        output = format_rules_for_display(rules, config_path=path)
        # Among custom rules, AAA should appear before ZZZ (sorted ascending by ID)
        pos_aaa = output.find("AAA-001")
        pos_zzz = output.find("ZZZ-001")
        assert pos_aaa != -1 and pos_zzz != -1
        assert pos_aaa < pos_zzz

    def test_no_config_path_no_source_tags(self, tmp_path):
        path = _make_config(tmp_path)
        rules = self._runtime_rules(path)
        output = format_rules_for_display(rules)  # no config_path
        assert "[default]" not in output
        assert "[custom]" not in output

    def test_empty_rules_returns_message(self):
        output = format_rules_for_display([])
        assert "No rules" in output or output == ""

    def test_shows_id_category_description(self, tmp_path):
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": "MY-CUSTOM-001",
                    "pattern": r"danger\s",
                    "category": "work_destruction",
                    "description": "My description",
                }
            ],
        )
        rules = self._runtime_rules(path)
        output = format_rules_for_display(rules, config_path=path)
        assert "MY-CUSTOM-001" in output
        assert "work_destruction" in output
        assert "My description" in output


# ---------------------------------------------------------------------------
# TestLoadRulesSameIdWarning
# ---------------------------------------------------------------------------


class TestLoadRulesSameIdWarning:
    """Tests that load_rules logs a warning and skips custom rules with default IDs."""

    def test_same_id_as_default_skipped(self, tmp_path):
        """A custom rule with the same ID as a default must be silently skipped."""
        default_id = _first_default_id()
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": default_id,
                    "pattern": r"custom_pattern\s",
                    "category": "work_destruction",
                    "description": "Sneaky override",
                }
            ],
        )
        rules = load_rules(path)
        # The default rule should be present exactly once
        matching = [r for r in rules if r["id"] == default_id]
        assert len(matching) == 1
        # It must be the default, not the custom override
        assert matching[0]["source"] == "default"

    def test_same_id_as_default_logs_warning(self, tmp_path):
        """A custom rule with a default ID must emit a warning log."""
        from unittest.mock import patch

        default_id = _first_default_id()
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": default_id,
                    "pattern": r"custom_pattern\s",
                    "category": "work_destruction",
                    "description": "Sneaky override",
                }
            ],
        )
        with patch("pacemaker.danger_bash_rules.log_warning") as mock_warn:
            load_rules(path)
        assert mock_warn.called
        warn_msg = mock_warn.call_args[0][1]
        assert default_id in warn_msg

    def test_unique_custom_id_not_skipped(self, tmp_path):
        """A custom rule with a unique ID must be included normally."""
        path = _make_config(
            tmp_path,
            rules=[
                {
                    "id": "MY-UNIQUE-001",
                    "pattern": r"unique_danger\s",
                    "category": "work_destruction",
                    "description": "Unique rule",
                }
            ],
        )
        rules = load_rules(path)
        assert any(r["id"] == "MY-UNIQUE-001" for r in rules)


# ---------------------------------------------------------------------------
# TestValidCategoriesConstant
# ---------------------------------------------------------------------------


class TestValidCategoriesConstant:
    """Tests for module-level VALID_CATEGORIES and MAX_PATTERN_DISPLAY_LEN constants.

    These verify the exported API contracts of the new constants.
    """

    def test_valid_categories_is_frozenset(self):
        assert isinstance(VALID_CATEGORIES, frozenset)

    def test_valid_categories_contains_expected(self):
        assert "work_destruction" in VALID_CATEGORIES
        assert "system_destruction" in VALID_CATEGORIES

    def test_max_pattern_display_len_is_positive_int(self):
        assert isinstance(MAX_PATTERN_DISPLAY_LEN, int)
        assert MAX_PATTERN_DISPLAY_LEN > 0
