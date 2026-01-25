#!/usr/bin/env python3
"""
Tests for CLI Blockage Statistics in Status Command.

Story #22: CLI Blockage Statistics in Status Command

Tests organized by acceptance criteria:
- AC3: Human-readable category labels (this file, first increment)
"""

from pathlib import Path

import pytest

# Add src to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ==============================================================================
# AC3: Human-Readable Category Labels Tests
# ==============================================================================


class TestBlockageCategoryLabels:
    """AC3: BLOCKAGE_CATEGORY_LABELS constant must map technical names to human-readable labels."""

    def test_blockage_category_labels_exists(self):
        """BLOCKAGE_CATEGORY_LABELS constant should be defined in constants module."""
        from pacemaker import constants

        assert hasattr(constants, "BLOCKAGE_CATEGORY_LABELS")
        assert constants.BLOCKAGE_CATEGORY_LABELS is not None

    def test_blockage_category_labels_is_dict(self):
        """BLOCKAGE_CATEGORY_LABELS should be a dictionary."""
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert isinstance(BLOCKAGE_CATEGORY_LABELS, dict)

    def test_blockage_category_labels_maps_intent_validation(self):
        """BLOCKAGE_CATEGORY_LABELS should map 'intent_validation' to 'Intent Validation'."""
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert BLOCKAGE_CATEGORY_LABELS.get("intent_validation") == "Intent Validation"

    def test_blockage_category_labels_maps_intent_validation_tdd(self):
        """BLOCKAGE_CATEGORY_LABELS should map 'intent_validation_tdd' to 'Intent TDD'."""
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert BLOCKAGE_CATEGORY_LABELS.get("intent_validation_tdd") == "Intent TDD"

    def test_blockage_category_labels_maps_pacing_tempo(self):
        """BLOCKAGE_CATEGORY_LABELS should map 'pacing_tempo' to 'Pacing Tempo'."""
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert BLOCKAGE_CATEGORY_LABELS.get("pacing_tempo") == "Pacing Tempo"

    def test_blockage_category_labels_maps_pacing_quota(self):
        """BLOCKAGE_CATEGORY_LABELS should map 'pacing_quota' to 'Pacing Quota'."""
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert BLOCKAGE_CATEGORY_LABELS.get("pacing_quota") == "Pacing Quota"

    def test_blockage_category_labels_maps_other(self):
        """BLOCKAGE_CATEGORY_LABELS should map 'other' to 'Other'."""
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert BLOCKAGE_CATEGORY_LABELS.get("other") == "Other"

    def test_blockage_category_labels_has_all_categories(self):
        """BLOCKAGE_CATEGORY_LABELS should have labels for all BLOCKAGE_CATEGORIES."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES, BLOCKAGE_CATEGORY_LABELS

        for category in BLOCKAGE_CATEGORIES:
            assert (
                category in BLOCKAGE_CATEGORY_LABELS
            ), f"Missing label for category: {category}"


# ==============================================================================
# AC1: Status Command Displays Blockage Section Tests
# ==============================================================================


class TestStatusCommandBlockageSection:
    """AC1: Status command must include a blockages section in its output."""

    def setup_method(self):
        """Create temporary files for each test."""
        import json
        import tempfile
        from pacemaker import database

        # Temporary config file
        self.temp_config = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.config_path = self.temp_config.name
        json.dump({"enabled": True}, self.temp_config)
        self.temp_config.close()

        # Temporary database
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        database.initialize_database(self.db_path)

        # Temporary state file
        self.temp_state = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.state_path = self.temp_state.name
        json.dump({}, self.temp_state)
        self.temp_state.close()

    def teardown_method(self):
        """Clean up temporary files."""
        Path(self.config_path).unlink(missing_ok=True)
        Path(self.db_path).unlink(missing_ok=True)
        Path(self.state_path).unlink(missing_ok=True)

    def test_status_output_includes_blockages_section_header(self):
        """Status command output should include 'Blockages (last hour):' section header."""
        from pacemaker import user_commands
        from unittest.mock import patch

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        assert result["success"] is True
        assert "Blockages (last hour):" in result["message"]

    def test_status_blockages_section_appears_after_existing_sections(self):
        """Blockages section should appear after existing status information."""
        from pacemaker import user_commands
        from unittest.mock import patch

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        message = result["message"]
        pace_maker_pos = message.find("Pace Maker:")
        blockages_pos = message.find("Blockages (last hour):")

        assert (
            blockages_pos > pace_maker_pos
        ), "Blockages section should appear after Pace Maker status"


# ==============================================================================
# AC2: Category Counts Displayed Tests
# ==============================================================================


class TestStatusCommandCategoryCounts:
    """AC2: Status command must display counts for each blockage category."""

    def setup_method(self):
        """Create temporary files for each test."""
        import json
        import tempfile
        from pacemaker import database

        self.temp_config = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.config_path = self.temp_config.name
        json.dump({"enabled": True}, self.temp_config)
        self.temp_config.close()

        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        database.initialize_database(self.db_path)

        self.temp_state = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.state_path = self.temp_state.name
        json.dump({}, self.temp_state)
        self.temp_state.close()

    def teardown_method(self):
        """Clean up temporary files."""
        Path(self.config_path).unlink(missing_ok=True)
        Path(self.db_path).unlink(missing_ok=True)
        Path(self.state_path).unlink(missing_ok=True)

    def test_status_displays_all_category_labels(self):
        """Status should display human-readable labels for all categories."""
        from pacemaker import user_commands
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS
        from unittest.mock import patch

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        # Check that all human-readable labels are present (except 'Other')
        for category, label in BLOCKAGE_CATEGORY_LABELS.items():
            if category != "other":
                assert label in result["message"], f"Missing label '{label}'"

    def test_status_displays_total_count(self):
        """Status should display total blockage count at bottom."""
        from pacemaker import user_commands
        from unittest.mock import patch

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        assert "Total:" in result["message"]

    def test_status_displays_accurate_counts(self):
        """Status should display accurate counts matching database records."""
        from pacemaker import user_commands, database
        from unittest.mock import patch

        # Record specific blockages
        for _ in range(5):
            database.record_blockage(
                db_path=self.db_path,
                category="intent_validation",
                reason="Test",
                hook_type="pre_tool_use",
                session_id="test",
            )
        for _ in range(3):
            database.record_blockage(
                db_path=self.db_path,
                category="pacing_quota",
                reason="Test",
                hook_type="post_tool_use",
                session_id="test",
            )

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        message = result["message"]
        blockages_start = message.find("Blockages (last hour):")
        assert blockages_start != -1
        blockages_section = message[blockages_start:]

        # Verify counts appear in the blockages section
        assert "5" in blockages_section  # intent_validation count
        assert "3" in blockages_section  # pacing_quota count
        assert "8" in blockages_section  # total count


# ==============================================================================
# AC4: Graceful Handling When No Blockages Tests
# ==============================================================================


class TestStatusCommandNoBlockages:
    """AC4: Status command must handle empty blockage data gracefully."""

    def setup_method(self):
        """Create temporary files for each test."""
        import json
        import tempfile
        from pacemaker import database

        self.temp_config = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.config_path = self.temp_config.name
        json.dump({"enabled": True}, self.temp_config)
        self.temp_config.close()

        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        database.initialize_database(self.db_path)

        self.temp_state = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.state_path = self.temp_state.name
        json.dump({}, self.temp_state)
        self.temp_state.close()

    def teardown_method(self):
        """Clean up temporary files."""
        Path(self.config_path).unlink(missing_ok=True)
        Path(self.db_path).unlink(missing_ok=True)
        Path(self.state_path).unlink(missing_ok=True)

    def test_status_succeeds_when_no_blockages(self):
        """Status command should succeed when no blockages exist."""
        from pacemaker import user_commands
        from unittest.mock import patch

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        assert result["success"] is True

    def test_status_maintains_structure_when_empty(self):
        """Status should maintain consistent output structure even with no blockages."""
        from pacemaker import user_commands
        from unittest.mock import patch

        with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
            result = user_commands.execute_command(
                command="status",
                config_path=self.config_path,
                db_path=self.db_path,
            )

        message = result["message"]
        assert "Blockages (last hour):" in message
        assert "Intent Validation:" in message
        assert "Total:" in message


# ==============================================================================
# AC5: Database Error Handling Tests
# ==============================================================================


class TestStatusCommandDatabaseErrors:
    """AC5: Status command must handle database errors gracefully."""

    def setup_method(self):
        """Create temporary files for each test."""
        import json
        import tempfile

        self.temp_config = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.config_path = self.temp_config.name
        json.dump({"enabled": True}, self.temp_config)
        self.temp_config.close()

        self.temp_state = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.state_path = self.temp_state.name
        json.dump({}, self.temp_state)
        self.temp_state.close()

    def teardown_method(self):
        """Clean up temporary files."""
        Path(self.config_path).unlink(missing_ok=True)
        Path(self.state_path).unlink(missing_ok=True)

    def test_status_shows_unavailable_on_db_error(self):
        """Status should show '(unavailable)' when database query fails."""
        from pacemaker import user_commands, database
        from unittest.mock import patch
        import tempfile

        # Create valid db for other operations but mock blockage stats to fail
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = temp_db.name
        temp_db.close()
        database.initialize_database(db_path)

        try:
            with patch.object(database, "get_hourly_blockage_stats") as mock_stats:
                mock_stats.side_effect = Exception("Database connection failed")

                with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
                    result = user_commands.execute_command(
                        command="status",
                        config_path=self.config_path,
                        db_path=db_path,
                    )

            assert result["success"] is True
            assert "(unavailable)" in result["message"]
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_status_does_not_fail_on_db_error(self):
        """Status command should succeed even when blockage query fails."""
        from pacemaker import user_commands, database
        from unittest.mock import patch
        import tempfile

        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = temp_db.name
        temp_db.close()
        database.initialize_database(db_path)

        try:
            with patch.object(database, "get_hourly_blockage_stats") as mock_stats:
                mock_stats.side_effect = Exception("Database connection failed")

                with patch("pacemaker.constants.DEFAULT_STATE_PATH", self.state_path):
                    result = user_commands.execute_command(
                        command="status",
                        config_path=self.config_path,
                        db_path=db_path,
                    )

            assert result["success"] is True
            assert "Pace Maker:" in result["message"]
        finally:
            Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
