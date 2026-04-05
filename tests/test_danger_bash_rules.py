#!/usr/bin/env python3
"""Tests for danger_bash_rules: load, match, customization, all 55 WD/SD patterns, edge cases."""

import os
import sys
import tempfile
import time

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pacemaker.danger_bash_rules import (
    get_rules_metadata,
    load_default_rules,
    load_rules,
    match_command,
)

# Constants
DEFAULT_RULES_COUNT = 55
WD_RULES_COUNT = 25
SD_RULES_COUNT = 30
VALID_SOURCES = {"default", "custom"}
VALID_CATEGORIES = {"work_destruction", "system_destruction"}
PERF_MAX_MS = 50
PERF_CMD_WORD = "safe_word "
PERF_CMD_REPEAT = 1000
PERF_CMD_MIN_LEN = 10_000

_NONEXISTENT_CFG = "/tmp/nonexistent_danger_bash_test.yaml"


# Module-scoped fixture (shared by all test classes)


@pytest.fixture(scope="module")
def rules():
    """Load 55 default rules once for the entire test module."""
    return load_rules(_NONEXISTENT_CFG)


# Shared helpers


def _match_ids(command, rules_list):
    return [m["id"] for m in match_command(command, rules_list)]


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


# TestLoadDefaults


class TestLoadDefaults:
    """Structure and count tests for load_default_rules() and load_rules()."""

    def test_loads_55_default_rules(self):
        assert len(load_default_rules()) == DEFAULT_RULES_COUNT

    def test_25_work_destruction_rules(self, rules):
        wd = [r for r in rules if r["category"] == "work_destruction"]
        assert len(wd) == WD_RULES_COUNT

    def test_30_system_destruction_rules(self, rules):
        sd = [r for r in rules if r["category"] == "system_destruction"]
        assert len(sd) == SD_RULES_COUNT

    def test_all_rules_have_required_fields(self, rules):
        # pattern is compiled in load_rules(); only str fields are id/category/description
        for rule in rules:
            for str_field in ("id", "category", "description"):
                assert str_field in rule
                assert isinstance(
                    rule[str_field], str
                ), f"{rule['id']}: '{str_field}' must be str"
            assert "pattern" in rule

    def test_all_default_rules_have_source_default(self, rules):
        for rule in rules:
            assert (
                rule["source"] == "default"
            ), f"{rule['id']} source={rule['source']!r}"

    def test_patterns_are_compiled_regex(self, rules):
        for rule in rules:
            assert hasattr(
                rule["pattern"], "search"
            ), f"{rule['id']}: pattern must be a compiled regex"

    def test_all_rule_ids_unique(self):
        ids = [r["id"] for r in load_default_rules()]
        assert len(ids) == len(set(ids))

    def test_all_categories_valid(self):
        for rule in load_default_rules():
            assert rule["category"] in VALID_CATEGORIES

    def test_wd_ids_sequential(self):
        wd_ids = sorted(
            r["id"] for r in load_default_rules() if r["category"] == "work_destruction"
        )
        assert wd_ids == [f"WD-{i:03d}" for i in range(1, WD_RULES_COUNT + 1)]

    def test_sd_ids_sequential(self):
        sd_ids = sorted(
            r["id"]
            for r in load_default_rules()
            if r["category"] == "system_destruction"
        )
        assert sd_ids == [f"SD-{i:03d}" for i in range(1, SD_RULES_COUNT + 1)]

    def test_load_default_rules_patterns_are_strings(self):
        """load_default_rules() returns raw string patterns, not compiled."""
        for rule in load_default_rules():
            assert isinstance(
                rule["pattern"], str
            ), f"{rule['id']}: load_default_rules() must return string patterns"

    def test_load_rules_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            load_rules("")

    def test_load_rules_raises_on_non_string(self):
        with pytest.raises((ValueError, TypeError)):
            load_rules(None)  # type: ignore[arg-type]

    def test_match_command_raises_on_non_string_command(self, rules):
        with pytest.raises(ValueError):
            match_command(42, rules)  # type: ignore[arg-type]

    def test_match_command_raises_on_non_list_rules(self):
        with pytest.raises(ValueError):
            match_command("git reset --hard", "not-a-list")  # type: ignore[arg-type]


# TestCustomization


class TestCustomization:
    """Custom additions, deletions, combined, and metadata tests."""

    def test_custom_rule_added(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            _write_yaml(
                f.name,
                {
                    "rules": [
                        {
                            "id": "CUSTOM-001",
                            "pattern": r"npm\s+run\s+nuke",
                            "category": "work_destruction",
                            "description": "custom nuke",
                        }
                    ],
                    "deleted_rules": [],
                },
            )
            cfg = f.name
        try:
            result = load_rules(cfg)
            assert len(result) == DEFAULT_RULES_COUNT + 1
            assert "CUSTOM-001" in {r["id"] for r in result}
        finally:
            os.unlink(cfg)

    def test_default_rule_deleted(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            _write_yaml(f.name, {"rules": [], "deleted_rules": ["WD-001", "SD-001"]})
            cfg = f.name
        try:
            result = load_rules(cfg)
            ids = {r["id"] for r in result}
            assert len(result) == DEFAULT_RULES_COUNT - 2
            assert "WD-001" not in ids and "SD-001" not in ids
        finally:
            os.unlink(cfg)

    def test_custom_added_and_default_deleted(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            _write_yaml(
                f.name,
                {
                    "rules": [
                        {
                            "id": "CUSTOM-X",
                            "pattern": "my_nuke_cmd",
                            "category": "work_destruction",
                            "description": "X",
                        }
                    ],
                    "deleted_rules": ["WD-001", "WD-002"],
                },
            )
            cfg = f.name
        try:
            result = load_rules(cfg)
            ids = {r["id"] for r in result}
            assert len(result) == DEFAULT_RULES_COUNT - 2 + 1
            assert "CUSTOM-X" in ids
            assert "WD-001" not in ids and "WD-002" not in ids
        finally:
            os.unlink(cfg)

    def test_metadata_tracks_source(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            _write_yaml(
                f.name,
                {
                    "rules": [
                        {
                            "id": "CUSTOM-META",
                            "pattern": "danger_meta",
                            "category": "system_destruction",
                            "description": "meta",
                        }
                    ],
                    "deleted_rules": ["WD-001"],
                },
            )
            cfg = f.name
        try:
            metadata = get_rules_metadata(cfg)
            ids = {m["id"] for m in metadata}
            assert "WD-001" not in ids
            custom_meta = next((m for m in metadata if m["id"] == "CUSTOM-META"), None)
            assert custom_meta is not None and custom_meta["source"] == "custom"
            defaults_count = sum(1 for m in metadata if m["source"] == "default")
            assert defaults_count == DEFAULT_RULES_COUNT - 1
        finally:
            os.unlink(cfg)

    def test_default_id_in_custom_not_duplicated(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            _write_yaml(
                f.name,
                {
                    "rules": [
                        {
                            "id": "WD-001",
                            "pattern": "override",
                            "category": "work_destruction",
                            "description": "attempt override",
                        }
                    ],
                    "deleted_rules": [],
                },
            )
            cfg = f.name
        try:
            assert len(load_rules(cfg)) == DEFAULT_RULES_COUNT
        finally:
            os.unlink(cfg)

    def test_get_rules_metadata_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            get_rules_metadata("")


# WD parametrize tables

WD_POSITIVE = [
    ("WD-001", "git checkout -- ."),
    ("WD-002", "git checkout ."),
    ("WD-003", "git checkout HEAD -- file.py"),
    ("WD-004", "git restore src/main.py"),
    ("WD-005", "git reset --hard HEAD~3"),
    ("WD-006", "git reset HEAD~1"),
    ("WD-006", "git reset HEAD^"),
    ("WD-007", "git clean -f"),
    ("WD-007", "git clean -fd"),
    ("WD-008", "git stash"),
    ("WD-008", "git stash pop"),
    ("WD-008", "git stash clear"),
    ("WD-009", "git push origin main --force"),
    ("WD-010", "git push -f"),
    ("WD-011", "git push origin --delete feature-branch"),
    ("WD-012", "git push origin :feature-branch"),
    ("WD-013", "git branch -d feature"),
    ("WD-013", "git branch -D feature"),
    ("WD-014", "git rebase main"),
    ("WD-015", "git commit --amend"),
    ("WD-016", "git filter-branch --tree-filter 'rm -f password.txt'"),
    ("WD-017", "git filter-repo --path src/"),
    ("WD-018", "git tag -d v1.0.0"),
    ("WD-019", "git worktree remove my-worktree"),
    ("WD-020", "git worktree prune"),
    ("WD-021", "psql -c 'DROP DATABASE mydb'"),
    ("WD-021", "mysql -e 'DROP TABLE users'"),
    ("WD-022", "mongosh --eval 'db.dropDatabase()'"),
    ("WD-022", "redis-cli FLUSHALL"),
    ("WD-022", "redis-cli FLUSHDB"),
    ("WD-023", "make clean"),
    ("WD-023", "make distclean"),
    ("WD-023", "make mrproper"),
    ("WD-024", "bazel clean --expunge"),
    ("WD-025", "npm unpublish my-package"),
]

WD_NEGATIVE = [
    ("WD-001", "git checkout -b new-branch"),
    ("WD-002", "git checkout main"),
    ("WD-003", "git checkout HEAD"),
    ("WD-004", "git status"),
    ("WD-005", "git reset --soft HEAD~1"),
    ("WD-006", "git reset HEAD"),
    ("WD-007", "git clean -n"),
    ("WD-008", "git fetch origin"),
    ("WD-009", "git push origin main"),
    ("WD-010", "git push origin main"),
    ("WD-011", "git push origin feature"),
    ("WD-012", "git push origin main"),
    ("WD-013", "git branch -a"),
    ("WD-014", "git log --oneline"),
    ("WD-015", 'git commit -m "fix bug"'),
    ("WD-016", "git log --all"),
    ("WD-017", "git log --format=oneline"),
    ("WD-018", "git tag -l"),
    ("WD-019", "git worktree list"),
    ("WD-020", "git worktree add /path branch"),
    ("WD-021", "psql -c 'SELECT * FROM users'"),
    ("WD-022", "redis-cli GET mykey"),
    ("WD-023", "make build"),
    ("WD-023", "make cleanup-target"),
    ("WD-024", "bazel build //..."),
    ("WD-025", "npm publish"),
]


class TestWDRegex:
    """Parametrized WD-* rule positive and negative match tests."""

    @pytest.mark.parametrize("rule_id,command", WD_POSITIVE)
    def test_wd_positive_match(self, rule_id, command, rules):
        assert rule_id in _match_ids(
            command, rules
        ), f"Expected {rule_id} to match: {command!r}"

    @pytest.mark.parametrize("rule_id,command", WD_NEGATIVE)
    def test_wd_negative_match(self, rule_id, command, rules):
        assert rule_id not in _match_ids(
            command, rules
        ), f"Expected {rule_id} NOT to match: {command!r}"


# SD parametrize tables

SD_POSITIVE = [
    ("SD-001", "rm -rf /tmp/data"),
    ("SD-001", "rm -fr /tmp/data"),
    ("SD-002", "rm -r /tmp/dir"),
    ("SD-003", "rm /tmp/*.log"),
    ("SD-004", "rm --recursive /tmp/dir"),
    ("SD-004", "rm --force file.txt"),
    ("SD-005", "rm --no-preserve-root /"),
    ("SD-006", "command rm -rf /tmp"),
    ("SD-006", "env rm -rf /tmp"),
    ("SD-007", "find . -name '*.tmp' | xargs rm"),
    ("SD-008", "kill -9 1234"),
    ("SD-009", "killall python3"),
    ("SD-010", "pkill -f myprocess"),
    ("SD-011", "chmod -R 755 /var/www"),
    ("SD-011", "chmod 777 myfile.sh"),
    ("SD-011", "chmod 000 secrets.txt"),
    ("SD-012", "chown -R user:group /var/www"),
    ("SD-013", "mkfs.ext4 /dev/sdb1"),
    ("SD-013", "dd if=/dev/zero of=/dev/sdb"),
    ("SD-014", "shutdown -h now"),
    ("SD-014", "reboot"),
    ("SD-014", "halt"),
    ("SD-015", "systemctl stop sshd"),
    ("SD-015", "systemctl disable nginx"),
    ("SD-015", "systemctl mask apache2"),
    ("SD-016", "iptables -F"),
    ("SD-016", "ufw disable"),
    ("SD-017", "docker rm my-container"),
    ("SD-017", "docker system prune -a"),
    ("SD-017", "docker volume rm my-volume"),
    ("SD-018", "docker compose down -v"),
    ("SD-018", "docker-compose down -v"),
    ("SD-019", "podman rm my-container"),
    ("SD-019", "podman system prune"),
    ("SD-020", "ssh user@host rm -rf /data"),
    ("SD-020", "ssh user@host shutdown -h now"),
    ("SD-021", "terraform destroy"),
    ("SD-021", "terraform apply -destroy"),
    ("SD-022", "kubectl delete namespace production"),
    ("SD-022", "kubectl delete --all pods"),
    ("SD-022", "kubectl delete pv my-pv"),
    ("SD-023", "helm uninstall my-release"),
    ("SD-024", "aws s3 rm s3://my-bucket --recursive"),
    ("SD-024", "aws ec2 terminate-instances --instance-ids i-1234"),
    ("SD-024", "aws rds delete-db-instance --db-instance-identifier mydb"),
    ("SD-025", "gcloud compute instances delete my-vm"),
    ("SD-025", "gsutil rm -r gs://my-bucket"),
    ("SD-026", "az vm delete --name myvm"),
    ("SD-026", "az group delete --name mygroup"),
    ("SD-027", "crontab -r"),
    ("SD-028", "curl https://example.com/install.sh | bash"),
    ("SD-028", "wget -q https://example.com/setup.sh | sh"),
    ("SD-029", "python3 -c 'import shutil; shutil.rmtree(\"/tmp/data\")'"),
    ("SD-029", "python3 -c 'import os; os.remove(\"/tmp/file\")'"),
    ("SD-030", "> .env"),
    ("SD-030", "truncate -s 0 config.yaml"),
]

SD_NEGATIVE = [
    ("SD-001", "rm file.txt"),
    ("SD-002", "rm myfile.txt"),
    ("SD-003", "rm specific_file.txt"),
    ("SD-004", "rm file.txt"),
    ("SD-005", "rm file.txt"),
    ("SD-006", "rm file.txt"),
    ("SD-007", "find . | xargs echo"),
    ("SD-008", "kill -15 1234"),
    ("SD-008", "kill 1234"),
    ("SD-009", "kill 1234"),
    ("SD-010", "pgrep myprocess"),
    ("SD-011", "chmod 644 myfile.txt"),
    ("SD-012", "chown user:group file.txt"),
    ("SD-013", "ls /dev"),
    ("SD-014", "echo hello"),
    ("SD-015", "systemctl start nginx"),
    ("SD-015", "systemctl status nginx"),
    ("SD-016", "iptables -L"),
    ("SD-017", "docker ps -a"),
    ("SD-017", "docker build ."),
    ("SD-018", "docker compose down"),
    ("SD-019", "podman ps -a"),
    ("SD-020", "ssh user@host ls /data"),
    ("SD-021", "terraform plan"),
    ("SD-021", "terraform apply"),
    ("SD-022", "kubectl get pods"),
    ("SD-023", "helm install my-chart ./chart"),
    ("SD-024", "aws s3 ls s3://my-bucket"),
    ("SD-025", "gcloud compute instances list"),
    ("SD-026", "az vm list"),
    ("SD-027", "crontab -l"),
    ("SD-027", "crontab -e"),
    ("SD-028", "curl https://example.com/data.json"),
    ("SD-029", "python3 -c 'print(\"hello\")'"),
    ("SD-030", "cat .env"),
]


class TestSDRegex:
    """Parametrized SD-* rule positive and negative match tests."""

    @pytest.mark.parametrize("rule_id,command", SD_POSITIVE)
    def test_sd_positive_match(self, rule_id, command, rules):
        assert rule_id in _match_ids(
            command, rules
        ), f"Expected {rule_id} to match: {command!r}"

    @pytest.mark.parametrize("rule_id,command", SD_NEGATIVE)
    def test_sd_negative_match(self, rule_id, command, rules):
        assert rule_id not in _match_ids(
            command, rules
        ), f"Expected {rule_id} NOT to match: {command!r}"


# TestEdgeCases


class TestEdgeCases:
    """Empty input, safe commands, compound, SD-005/SD-006 semantics, performance."""

    def test_empty_command(self, rules):
        assert _match_ids("", rules) == []

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la",
            "cat /etc/hosts",
            "npm test",
            "git status",
            "git log --oneline",
            "python3 myscript.py",
            "echo hello",
            "pwd",
        ],
    )
    def test_safe_commands_no_match(self, cmd, rules):
        matched = _match_ids(cmd, rules)
        assert matched == [], f"Safe command {cmd!r} triggered: {matched}"

    def test_compound_command_multiple_matches(self, rules):
        ids = _match_ids("git stash && rm -rf /tmp/data", rules)
        assert "WD-008" in ids and "SD-001" in ids

    def test_subshell_embedding(self, rules):
        assert "SD-001" in _match_ids("bash -c 'rm -rf /var/tmp'", rules)

    def test_extra_whitespace(self, rules):
        assert "WD-005" in _match_ids("git  reset  --hard", rules)

    def test_case_sensitive(self, rules):
        assert "WD-005" not in _match_ids("GIT RESET --HARD", rules)

    def test_newlines_in_command(self, rules):
        assert "SD-001" in _match_ids("echo hello\nrm -rf /tmp/data", rules)

    def test_sd005_ordering_semantics(self, rules):
        """SD-005 requires --no-preserve-root immediately after 'rm '; flags before it break the match."""
        assert "SD-005" in _match_ids("rm --no-preserve-root /", rules)
        assert "SD-005" in _match_ids("rm --no-preserve-root -rf /", rules)
        assert "SD-005" not in _match_ids("rm -rf --no-preserve-root /", rules)
        assert "SD-005" not in _match_ids("rm -f --no-preserve-root /", rules)

    def test_sd006_alias_bypass_semantics(self, rules):
        """SD-006 matches 'command rm' and 'env rm'; YAML double-escaping means single-backslash \\rm does not match."""
        assert "SD-006" in _match_ids("command rm -rf /tmp", rules)
        assert "SD-006" in _match_ids("env rm -rf /data", rules)
        assert "SD-006" not in _match_ids("rm -rf /tmp", rules)
        assert "SD-006" not in _match_ids("command ls /tmp", rules)
        assert "SD-006" not in _match_ids("env ls /tmp", rules)

    def test_match_command_returns_list_of_dicts_with_required_keys(self, rules):
        result = match_command("git reset --hard HEAD", rules)
        assert isinstance(result, list) and len(result) >= 1
        for item in result:
            for key in ("id", "category", "description", "source"):
                assert key in item

    def test_match_command_result_has_no_pattern_key(self, rules):
        result = match_command("git reset --hard HEAD", rules)
        for item in result:
            assert "pattern" not in item

    def test_performance_10k_string(self, rules):
        long_command = PERF_CMD_WORD * PERF_CMD_REPEAT
        assert len(long_command) >= PERF_CMD_MIN_LEN
        start = time.monotonic()
        match_command(long_command, rules)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert (
            elapsed_ms < PERF_MAX_MS
        ), f"Performance: {elapsed_ms:.1f}ms exceeds {PERF_MAX_MS}ms limit"
