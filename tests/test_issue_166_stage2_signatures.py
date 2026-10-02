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
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from pacemaker.intent_validator import (  # noqa: E402
    _read_target_file_for_review as real_read_target_file,
)
from pacemaker.stage2_signatures import (  # noqa: E402
    STAGE2_SIGNATURES_MAX_CHARS,
    STAGE2_SIGNATURES_MAX_ENTRIES,
    STAGE2_SIGNATURES_MAX_MODULE_BYTES,
    Candidate,
    _extract_candidates,
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
        # Docstrings of OTHER modules are not copied (review L1).
        assert "Log warning message." not in section


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

    def test_async_method_called_on_self(self, tmp_path):
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
            str(root / "src" / "pkg" / "mod.py"), "await self.fetch('u')\n"
        )
        assert "async def fetch(self, url, timeout=5)" in section
        assert "method of Svc" in section

    def test_classmethod_style_call_on_cls(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "class Svc:\n    def build(cls, spec):\n        pass\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "cls.build(1)\n"
        )
        assert "def build(cls, spec)" in section

    @pytest.mark.parametrize(
        "call",
        [
            "d.get('k')",  # dict.get
            "', '.join(parts)",  # str.join (constant receiver)
            "cfg.update(other)",  # dict.update
            "svc.fetch('u')",  # unknown receiver
            "self.helper.get('k')",  # attribute of self: type unknown
            "make().get('k')",  # call result receiver
            "items[0].join(x)",  # subscript receiver
        ],
    )
    def test_method_is_not_matched_by_bare_name_for_other_receivers(
        self, tmp_path, call
    ):
        """A project class that happens to define get/join/update/fetch must
        not be shown as the callee of dict.get, str.join, ... (#166 review
        M1): the section would claim "trust these" for the wrong function."""
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "class Store:\n"
                    "    def get(self, key, default, strict):\n        pass\n"
                    "    def join(self, left, right, how):\n        pass\n"
                    "    def update(self, a, b, c):\n        pass\n"
                    "    async def fetch(self, url, timeout=5):\n        pass\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), f"x = {call}\n"
        )
        assert section == ""

    def test_self_call_prefers_the_method_over_a_same_named_function(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "def run(a):\n    pass\n\n"
                    "class Job:\n    def run(self, a, b):\n        pass\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src" / "pkg" / "mod.py"), "self.run(1, 2)\n"
        )
        assert "def run(self, a, b)" in section
        assert "def run(a)" not in section

    def test_header_is_best_effort_not_an_unconditional_promise(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1)\n"
        )
        assert "best-effort" in section
        assert "matched by name" in section
        assert "may not be the exact callee" in section

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


class TestLinearTimeFallbacks:
    """Review H1: the regex fallbacks run on code that does NOT parse (a
    typical Edit fragment), up to 100k chars, inside the PreToolUse
    deadline. A quadratic scan took 5 s at 20k chars and over 30 s at 50k,
    so the gate could be killed and the edit go through unreviewed."""

    @pytest.mark.parametrize(
        "code",
        [
            "x = " + "a" * 50_000 + " , (1,",  # identifier run, not a call
            "x = " + "a." * 25_000 + " , (1,",  # dotted run, not a call
            "f(" * 25_000,  # unclosed, deeply nested calls
            "def " * 25_000 + "(",  # keyword spam
            "from " + "." * 50_000 + "\n(",  # relative-import dots
            "a" + " " * 50_000 + "b ,(",  # long whitespace run
        ],
        ids=["ident-run", "dotted-run", "nested-calls", "def-spam", "dots", "spaces"],
    )
    def test_non_parsing_fragment_finishes_fast(self, tmp_path, code):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        started = time.monotonic()
        build_called_signatures_section(str(root / "src/pkg/mod.py"), code)
        assert time.monotonic() - started < 1.0

    def test_non_parsing_module_on_disk_is_handled_fast(self, tmp_path):
        broken = "def helper(a, b):\n    pass\n\nthis is not python (\n" + "z" * 250_000
        root = _project(tmp_path, {"src/pkg/mod.py": broken})
        started = time.monotonic()
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1, 2)\n"
        )
        assert time.monotonic() - started < 1.0
        assert "def helper(a, b)" in section

    def test_regex_fallback_still_finds_dotted_module_calls(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def compute(x, y):\n    return x\n",
                "src/pkg/main.py": "import pkg.util\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"), "total = (pkg.util.compute(1,\n"
        )
        assert "def compute(x, y)" in section

    def test_regex_fallback_ignores_a_name_glued_to_a_number_or_dot(self, tmp_path):
        """`1e5(`, `.5(`: not calls of a project function named e5."""
        root = _project(tmp_path, {"src/pkg/mod.py": "def e5(a):\n    pass\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "x = (1e5(2,\n"
        )
        assert section == ""


class TestHardcodedValuesAreNotCopied:
    """Review L1: stored-secret masking only covers values registered in the
    secrets store, so a hardcoded credential in a default argument or a
    docstring of an imported module must never be copied verbatim."""

    CLIENT = (
        "def connect(host, api_key='sk-live-abcdef123456', blob=b'xyzsecretbytes',"
        ' retries=3, mode=None, *, token="tok-kwonly-998877",'
        " opts=('sk-nested-111222', 2)):\n"
        '    """Open a connection with key sk-docstring-secret-5566."""\n'
    )

    def _section(self, tmp_path, call="connect('h')\n"):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/client.py": self.CLIENT,
                "src/pkg/main.py": "from pkg.client import connect\n",
            },
        )
        return build_called_signatures_section(str(root / "src/pkg/main.py"), call)

    def test_string_and_bytes_defaults_render_as_a_placeholder(self, tmp_path):
        section = self._section(tmp_path)
        assert (
            "def connect(host, api_key='...', blob='...', retries=3, mode=None, "
            "*, token='...', opts=('...', 2))"
        ) in section

    def test_no_secret_looking_value_reaches_the_section(self, tmp_path):
        section = self._section(tmp_path)
        for leaked in (
            "sk-live-abcdef123456",
            "xyzsecretbytes",
            "tok-kwonly-998877",
            "sk-nested-111222",
            "sk-docstring-secret-5566",
        ):
            assert leaked not in section

    def test_imported_module_docstring_is_omitted(self, tmp_path):
        assert "Open a connection" not in self._section(tmp_path)

    def test_class_init_defaults_are_redacted_too(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/client.py": (
                    "class Client:\n"
                    "    def __init__(self, url, password='hunter2-hunter2'):\n"
                    "        pass\n"
                ),
                "src/pkg/main.py": "from pkg.client import Client\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"), "Client('u')\n"
        )
        assert "def __init__(self, url, password='...')" in section
        assert "hunter2-hunter2" not in section

    def test_edited_files_own_defaults_are_redacted_but_its_docstring_is_kept(
        self, tmp_path
    ):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "def helper(a, key='sk-own-file-424242'):\n"
                    '    """Do the helping."""\n'
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1)\n"
        )
        assert "def helper(a, key='...')" in section
        assert "sk-own-file-424242" not in section
        assert "Do the helping." in section

    def test_class_keyword_and_base_literals_are_redacted(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/client.py": (
                    "class Svc(Base('x-BASE-SECRET'), api_key='sk-CLASSKW-SECRET',"
                    " meta=b'bytes-kw-secret'):\n"
                    "    def __init__(self, a):\n        pass\n"
                ),
                "src/pkg/main.py": "from pkg.client import Svc\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"), "Svc(1)\n"
        )
        assert "class Svc(Base('...'), api_key='...', meta='...')" in section
        for leaked in ("x-BASE-SECRET", "sk-CLASSKW-SECRET", "bytes-kw-secret"):
            assert leaked not in section

    def test_non_string_class_bases_and_keywords_are_untouched(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": (
                    "class Svc(Base, mixins.Other, metaclass=Meta, flag=True, n=3):\n"
                    "    pass\n"
                )
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "Svc()\n"
        )
        assert (
            "class Svc(Base, mixins.Other, metaclass=Meta, flag=True, n=3)" in section
        )

    def test_triple_quoted_default_in_the_fallback_is_one_placeholder(self, tmp_path):
        broken = (
            "def helper(a, doc='''sk-triple-secret''', b=1):\n"
            "    pass\n\nthis is not python (\n"
        )
        root = _project(tmp_path, {"src/pkg/mod.py": broken})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1)\n"
        )
        assert "def helper(a, doc='...', b=1)" in section
        assert "sk-triple-secret" not in section

    def test_non_string_defaults_are_untouched(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/mod.py": "def f(a=1, b=2.5, c=None, d=True, e=(1, 2)):\n    pass\n"
            },
        )
        section = build_called_signatures_section(str(root / "src/pkg/mod.py"), "f()\n")
        assert "def f(a=1, b=2.5, c=None, d=True, e=(1, 2))" in section

    def test_regex_fallback_def_lines_are_redacted_too(self, tmp_path):
        broken = (
            "def helper(a, key='sk-fallback-777', other=\"tok-fallback-888\"):\n"
            "    pass\n\nthis is not python (\n"
        )
        root = _project(tmp_path, {"src/pkg/mod.py": broken})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"), "helper(1)\n"
        )
        assert "def helper(a, key='...', other='...')" in section
        assert "sk-fallback-777" not in section
        assert "tok-fallback-888" not in section

    def test_unclosed_quotes_on_a_huge_def_line_stay_fast(self, tmp_path):
        broken = "def helper(a, k=" + "'" * 200_000 + "):\n    pass\n(\n"
        root = _project(tmp_path, {"src/pkg/mod.py": broken})
        started = time.monotonic()
        build_called_signatures_section(str(root / "src/pkg/mod.py"), "helper(1)\n")
        assert time.monotonic() - started < 1.0


class TestTargetContent:
    """Review L2: the hook already read the target file for the diff and the
    surrounding context; the builder must reuse that read, never read the
    edited file a second time."""

    def _spy(self):
        return patch(
            "pacemaker.stage2_signatures._read_target_file_for_review",
            wraps=real_read_target_file,
        )

    def test_given_content_replaces_the_disk_read(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "x = 1\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"),
            "helper(1)\n",
            _target_content="def helper(a, b):\n    pass\n",
        )
        assert "def helper(a, b)" in section

    def test_the_edited_file_is_not_read_when_content_is_given(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        target = str(root / "src/pkg/mod.py")
        with self._spy() as spy:
            section = build_called_signatures_section(
                target, "helper(1)\n", _target_content="def helper(a):\n    pass\n"
            )
        assert "def helper(a)" in section
        assert [c for c in spy.call_args_list if c.args[0] == target] == []

    def test_without_content_the_edited_file_is_read_exactly_once(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "def helper(a):\n    pass\n\ndef other(a):\n    pass\n"},
        )
        target = str(root / "src/pkg/mod.py")
        with self._spy() as spy:
            build_called_signatures_section(target, "helper(1)\nother(2)\n")
        assert len([c for c in spy.call_args_list if c.args[0] == target]) == 1

    def test_empty_content_is_content_not_a_missing_file(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "def helper(a):\n    pass\n"})
        target = str(root / "src/pkg/mod.py")
        with self._spy() as spy:
            section = build_called_signatures_section(
                target, "helper(1)\n", _target_content=""
            )
        assert section == ""
        assert [c for c in spy.call_args_list if c.args[0] == target] == []

    def test_other_modules_are_still_read_once_each(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "def a1(x):\n    pass\n\ndef a2(x):\n    pass\n",
                "src/pkg/main.py": "from pkg.util import a1, a2\n",
            },
        )
        target = str(root / "src/pkg/main.py")
        util = str(root / "src/pkg/util.py")
        with self._spy() as spy:
            section = build_called_signatures_section(
                target,
                "a1(1)\na2(2)\n",
                _target_content="from pkg.util import a1, a2\n",
            )
        assert "def a1(x)" in section and "def a2(x)" in section
        assert len([c for c in spy.call_args_list if c.args[0] == util]) == 1

    def test_imports_from_content_and_new_code_both_count(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/one.py": "def first(x):\n    pass\n",
                "src/pkg/two.py": "def second(x):\n    pass\n",
                "src/pkg/main.py": "",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"),
            "from pkg.two import second\nfirst(1)\nsecond(2)\n",
            _target_content="from pkg.one import first\n",
        )
        assert "def first(x)" in section and "def second(x)" in section


class TestModuleSizeCap:
    """Review L3: a 2 MB module must not cost seconds to parse."""

    FILLER = "#" * (STAGE2_SIGNATURES_MAX_MODULE_BYTES + 10)

    def test_the_cap_is_about_300_kb(self):
        assert 200_000 <= STAGE2_SIGNATURES_MAX_MODULE_BYTES <= 400_000

    def test_module_over_the_cap_is_not_used(self, tmp_path):
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/big.py": "def helper(a):\n    pass\n" + self.FILLER,
                "src/pkg/main.py": "from pkg.big import helper\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"), "helper(1)\n"
        )
        assert section == ""

    def test_module_just_under_the_cap_is_used(self, tmp_path):
        filler = "#" * (STAGE2_SIGNATURES_MAX_MODULE_BYTES - 1000)
        root = _project(
            tmp_path,
            {
                "src/pkg/__init__.py": "",
                "src/pkg/big.py": "def helper(a):\n    pass\n" + filler,
                "src/pkg/main.py": "from pkg.big import helper\n",
            },
        )
        section = build_called_signatures_section(
            str(root / "src/pkg/main.py"), "helper(1)\n"
        )
        assert "def helper(a)" in section

    def test_oversized_edited_file_on_disk_is_not_used(self, tmp_path):
        root = _project(
            tmp_path,
            {"src/pkg/mod.py": "def helper(a):\n    pass\n" + self.FILLER},
        )
        target = str(root / "src/pkg/mod.py")
        assert build_called_signatures_section(target, "helper(1)\n") == ""

    def test_oversized_given_content_is_ignored(self, tmp_path):
        root = _project(tmp_path, {"src/pkg/mod.py": "x = 1\n"})
        section = build_called_signatures_section(
            str(root / "src/pkg/mod.py"),
            "helper(1)\n",
            _target_content="def helper(a):\n    pass\n" + self.FILLER,
        )
        assert section == ""


class TestExtractCandidates:
    """Review L4: the set of names the code defines is used inside the
    extraction only; callers get just the candidate list."""

    def test_returns_the_ordered_candidate_list_only(self):
        result = _extract_candidates("a(1)\nb.c(2)\nprint(3)\n")
        assert result == [Candidate(None, "a"), Candidate("b", "c")]

    def test_names_the_code_defines_are_dropped_inside(self):
        assert _extract_candidates("def a(x):\n    pass\n\na(1)\nz(2)\n") == [
            Candidate(None, "z")
        ]
