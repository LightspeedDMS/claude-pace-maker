"""Issue #182: a config.json created from scratch must hold only the key(s) the
command sets, so every other setting keeps following DEFAULT_CONFIG.

Real files in tmp_path; no mocks.
"""

import json

from pacemaker.user_commands import execute_command


class TestConfigCreatedFromScratch:
    def test_off_writes_only_the_enabled_key(self, tmp_path):
        cfg = tmp_path / "pm" / "config.json"
        result = execute_command("off", str(cfg), None, None)
        assert result["success"], result
        assert json.loads(cfg.read_text()) == {"enabled": False}

    def test_on_persists_enabled_true_even_though_equal_to_default(self, tmp_path):
        # The claude-usage monitor reads {} as "disabled", and the user's
        # explicit choice must be persisted.
        cfg = tmp_path / "pm" / "config.json"
        result = execute_command("on", str(cfg), None, None)
        assert result["success"], result
        assert json.loads(cfg.read_text()) == {"enabled": True}

    def test_fresh_config_from_other_command_also_carries_enabled(self, tmp_path):
        # The monitor reads config.get("enabled", False): a fresh file without
        # "enabled" would show pace-maker disabled although it is enabled.
        cfg = tmp_path / "pm" / "config.json"
        result = execute_command("intent-validation", str(cfg), None, "on")
        assert result["success"], result
        assert json.loads(cfg.read_text()) == {
            "intent_validation_enabled": True,
            "enabled": True,
        }


class TestDefaultsStillResolve:
    def test_gate_lookup_resolves_default_after_scratch_write(self, tmp_path):
        from pacemaker.constants import DEFAULT_CONFIG
        from pacemaker.hook import load_config

        cfg = tmp_path / "pm" / "config.json"
        execute_command("off", str(cfg), None, None)
        loaded = load_config(str(cfg))
        key = "reasoning_summary_intent_models"
        # Same lookup the Write/Edit and Bash gates perform.
        assert loaded.get(key, DEFAULT_CONFIG.get(key, [])) == DEFAULT_CONFIG[key]
        assert loaded["enabled"] is False

    def test_status_on_missing_config_does_not_create_file(self, tmp_path):
        cfg = tmp_path / "pm" / "config.json"
        result = execute_command("status", str(cfg), None, None)
        assert result["success"], result
        assert not cfg.exists()


class TestExistingConfigUntouched:
    def test_existing_explicit_config_only_gets_the_set_key(self, tmp_path):
        cfg = tmp_path / "config.json"
        original = {
            "enabled": True,
            "reasoning_summary_intent_models": ["claude-opus-5-5"],
        }
        cfg.write_text(json.dumps(original))
        result = execute_command("off", str(cfg), None, None)
        assert result["success"], result
        assert json.loads(cfg.read_text()) == {**original, "enabled": False}
