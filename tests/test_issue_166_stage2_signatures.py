"""
Bug #166 -- Stage 2 reviewer false positives from missing signatures of
called functions. ``build_called_signatures_section`` renders a capped
"SIGNATURES OF CALLED FUNCTIONS" section for Write/Edit on Python files.

Real files on disk (tmp projects plus this very repo); nothing is mocked.
"""

import os

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from pacemaker.stage2_signatures import (  # noqa: E402
    STAGE2_SIGNATURES_MAX_CHARS,
    STAGE2_SIGNATURES_MAX_ENTRIES,
    build_called_signatures_section,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADER = "SIGNATURES OF CALLED FUNCTIONS"


def _project(tmp_path: Path, files: dict) -> Path:
    """Create a tiny project: pyproject.toml marker plus ``files`` (relative
    path -> content) and return the project root."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


class TestLogWarningRepro:
    """The live #166 false positive: the reviewer said a log_warning mock's
    ``args[1]`` must be ``args[0]``; the real signature puts the message at
    index 1."""

    def test_patch_target_string_resolves_through_the_reexport(self):
        test_file = REPO_ROOT / "tests" / "test_issue_161_state_agent_ids.py"
        new_code = (
            'with patch("pacemaker.langfuse.state.log_warning") as warn:\n'
            '    manager.read("subagent-../x")\n'
            'assert all("unsafe" in c.args[1].lower() for c in warn.call_args_list)\n'
        )
        section = build_called_signatures_section(str(test_file), new_code)
        assert HEADER in section
        assert "def log_warning(component: str, message: str" in section

    def test_direct_import_in_the_edited_file_also_resolves(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/logger.py": (
                    "def log_warning(component, message, exc=None):\n"
                    '    """Log warning message."""\n'
                ),
                "tests/test_x.py": "from pkg.logger import log_warning\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "tests" / "test_x.py"), 'log_warning("c", "m")\n'
        )
        assert "def log_warning(component, message, exc=None)" in section
        assert "Log warning message." in section


class TestResolution:
    def test_same_file_function_with_first_docstring_line(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "def helper(a, b=2, *args, key=None, **kw) -> int:\n"
                    '    """Add things.\n\n    More detail that is not shown.\n    """\n'
                    "    return a\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "x = helper(1)\n"
        )
        assert "def helper(a, b=2, *args, key=None, **kw) -> int" in section
        assert "Add things." in section
        assert "More detail" not in section

    def test_long_docstring_first_line_is_dropped(self, tmp_path):
        long_doc = "d" * 300
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": f'def helper(a):\n    """{long_doc}"""\n'},
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "helper(1)\n"
        )
        assert "def helper(a)" in section
        assert long_doc not in section

    def test_async_function_and_method_by_attribute_name(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "class Svc:\n"
                    "    async def fetch(self, url, timeout=5):\n"
                    "        pass\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "await svc.fetch('u')\n"
        )
        assert "async def fetch(self, url, timeout=5)" in section

    def test_class_shows_bases_and_init_signature(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "class Widget(Base):\n"
                    '    """A widget."""\n\n'
                    "    def __init__(self, name, size=1):\n"
                    "        pass\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "w = Widget('a')\n"
        )
        assert "class Widget(Base)" in section
        assert "def __init__(self, name, size=1)" in section

    def test_from_import_of_project_module(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/main.py": "from pkg.util import compute as c\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "main.py"), "c(1, 2)\n"
        )
        assert "def compute(x, y)" in section
        assert "src/pkg/util.py" in section

    def test_import_module_alias_attribute_call(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/main.py": "import pkg.util as u\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "main.py"), "u.compute(1, 2)\n"
        )
        assert "def compute(x, y)" in section

    def test_dotted_module_attribute_call_without_alias(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/main.py": "import pkg.util\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "main.py"), "pkg.util.compute(1, 2)\n"
        )
        assert "def compute(x, y)" in section

    def test_relative_import(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/sub/__init__.py": "",
                "src/pkg/sub/main.py": "from ..util import compute\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "sub" / "main.py"), "compute(1, 2)\n"
        )
        assert "def compute(x, y)" in section

    def test_from_package_import_module_then_attribute_call(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/main.py": "from pkg import util\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "main.py"), "util.compute(1, 2)\n"
        )
        assert "def compute(x, y)" in section

    def test_imports_added_by_the_new_code_itself_count(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/main.py": "pass\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "main.py"),
            "from pkg.util import compute\ncompute(1, 2)\n",
        )
        assert "def compute(x, y)" in section

    def test_third_party_and_stdlib_imports_never_resolve(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/main.py": "import os\nfrom json import dumps\nimport requests\n"},
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "main.py"),
            "os.path.join('a')\ndumps({})\nrequests.get('u')\n",
        )
        assert section == ""

    def test_names_defined_in_the_new_code_are_not_listed(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "def helper(a):\n    pass\n"},
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"),
            "def helper(a, b):\n    pass\n\nhelper(1, 2)\n",
        )
        assert section == ""

    def test_builtins_are_ignored(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def len(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "print(len([1]))\n"
        )
        assert section == ""

    def test_nothing_resolvable_means_no_section(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "x = 1\n"})
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "mystery(1)\n"
        )
        assert section == ""

    def test_empty_new_code_means_no_section(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        assert build_called_signatures_section(str(root / "src/pkg/mod.py"), "") == ""

    def test_missing_target_file_still_resolves_via_the_new_codes_imports(
        self, tmp_path
    ):
        """A Write of a brand-new file: nothing on disk yet, but its own
        imports name project modules."""
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "new_mod.py"),
            "from pkg.util import compute\ncompute(1, 2)\n",
        )
        assert "def compute(x, y)" in section


class TestGating:
    def test_non_python_file_gets_no_section(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.js": "function helper(a) {}\n"})
        assert (
            build_called_signatures_section(str(root / "src/pkg/mod.js"), "helper(1)")
            == ""
        )

    @pytest.mark.parametrize("name", ["notes.md", "run.sh", "Makefile", "data.json"])
    def test_other_extensions_get_no_section(self, tmp_path, name):
        root = _project(tmp_path, {name: "def helper(a): pass\n"})
        assert build_called_signatures_section(str(root / name), "helper(1)") == ""

    def test_past_deadline_gets_no_section(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"),
            "helper(1)\n",
            _deadline=time.monotonic() - 1,
        )
        assert section == ""

    def test_future_deadline_is_fine(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"),
            "helper(1)\n",
            _deadline=time.monotonic() + 60,
        )
        assert "def helper(a)" in section

    def test_deadline_expiring_midway_drops_the_whole_section(
        self, tmp_path, monkeypatch
    ):
        """The deadline is re-checked between resolutions: once it passes
        mid-way, nothing partial is returned."""
        import itertools

        import pacemaker.stage2_signatures as sig

        names = [f"a{i}" for i in range(12)]
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "".join(f"def {n}(x):\n    pass\n\n" for n in names)},
        )
        # A fake clock that advances by 1 on every read: the first check
        # (0.0) passes, but a deadline of 3.0 is crossed long before all 12
        # names are resolved, whatever the exact number of reads is.
        ticks = itertools.count()
        monkeypatch.setattr(sig.time, "monotonic", lambda: float(next(ticks)))
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"),
            "".join(f"{n}(1)\n" for n in names),
            _deadline=3.0,
        )
        assert section == ""

    def test_secret_like_module_path_is_never_read(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/my_secrets.py": "def get_token(user, password='hunter2x'):\n    pass\n",
                "src/pkg/main.py": "from pkg.my_secrets import get_token\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"), "get_token('u')\n"
        )
        assert section == ""
        assert "hunter2x" not in section

    def test_secret_like_target_file_path_gets_no_section(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/credentials.py": "def helper(a):\n    pass\n"},
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/credentials.py"), "helper(1)\n"
        )
        assert section == ""

    def test_oversized_file_on_disk_is_skipped(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "def helper(a):\n    pass\n" + "#" * 2_100_000},
        )
        assert (
            build_called_signatures_section(str(root / "src/pkg/mod.py"), "helper(1)")
            == ""
        )


class TestCap:
    def _many(self, tmp_path, count, pad=0):
        defs = "".join(
            f"def func_{i}(argument_one, argument_two, {'p' * max(pad, 1)}=None):\n"
            "    pass\n\n"
            for i in range(count)
        )
        root = _project(tmp_path, {"src/pkg/mod.py": defs})
        calls = "".join(f"func_{i}(1, 2)\n" for i in range(count))
        return str(root / "src/pkg/mod.py"), calls

    def test_section_never_exceeds_the_char_cap(self, tmp_path):
        path, calls = self._many(tmp_path, 40, pad=60)
        section = build_called_signatures_section(path, calls)
        assert HEADER in section
        assert len(section) <= STAGE2_SIGNATURES_MAX_CHARS

    def test_entry_count_is_capped(self, tmp_path):
        path, calls = self._many(tmp_path, 40)
        section = build_called_signatures_section(path, calls)
        assert section.count("def func_") <= STAGE2_SIGNATURES_MAX_ENTRIES

    def test_omission_is_noted_when_something_was_dropped(self, tmp_path):
        path, calls = self._many(tmp_path, 40)
        section = build_called_signatures_section(path, calls)
        assert "more omitted" in section

    def test_first_called_names_win(self, tmp_path):
        path, calls = self._many(tmp_path, 40)
        section = build_called_signatures_section(path, calls)
        assert "def func_0(" in section
        assert "def func_39(" not in section

    def test_one_huge_signature_is_truncated_not_dropped_or_overflowing(self, tmp_path):
        params = ", ".join(f"parameter_number_{i}=None" for i in range(80))
        root = _project(tmp_path, {"src/pkg/mod.py": f"def big({params}):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "big()\n"
        )
        assert "def big(parameter_number_0=None" in section
        assert len(section) <= STAGE2_SIGNATURES_MAX_CHARS

    def test_same_definition_listed_once(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1)\nhelper(2)\nx.helper(3)\n"
        )
        assert section.count("def helper(a)") == 1


class TestParseFailureFallbacks:
    def test_unparseable_new_code_falls_back_to_regex(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a, b):\n    pass\n"})
        # A fragment that is not valid Python on its own (unclosed paren).
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "result = helper(1,\n    2\n"
        )
        assert "def helper(a, b)" in section

    def test_regex_fallback_ignores_keywords_and_definitions(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "def helper(a):\n    pass\n\ndef other(a):\n    pass\n"},
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"),
            "def other(a, b):\n    if (a):\n        return helper(\n",
        )
        assert "def helper(a)" in section
        assert "def other(" not in section

    def test_syntax_error_on_disk_falls_back_to_def_line_scan(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "def helper(a, b):\n    pass\n\nthis is not python (\n"},
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1, 2)\n"
        )
        assert "def helper(a, b)" in section

    def test_null_bytes_in_new_code_do_not_crash(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1)\x00\n"
        )
        assert "def helper(a)" in section

    def test_non_string_input_fails_safe_to_empty(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        assert build_called_signatures_section(str(root / "src/pkg/mod.py"), 123) == ""
        assert build_called_signatures_section(None, "helper(1)") == ""

    def test_huge_new_code_is_bounded(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        started = time.monotonic()
        build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "x = 1\n" * 500_000 + "helper(1)\n"
        )
        assert time.monotonic() - started < 5
