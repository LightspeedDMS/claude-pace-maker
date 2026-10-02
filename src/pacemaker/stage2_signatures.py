"""Bug #166: the "SIGNATURES OF CALLED FUNCTIONS" section of the Stage 2 prompt.

Stage 2 reviewers sometimes flag correct code because they cannot see the
signatures of the functions the diff calls (live: a reviewer claimed a
``log_warning`` mock's ``args[1]`` must be ``args[0]``, but
``log_warning(component, message)`` puts the message at ``args[1]``).

``build_called_signatures_section`` finds the names called in the ADDED code
(``ast`` where the fragment parses, a conservative regex otherwise), resolves
their ``def``/``class`` lines from the same file on disk and from project
modules that file imports, and renders a capped section.

Fail-safe by contract: any error, a non-Python file, nothing resolvable, or a
passed gate deadline yields ``""`` (no section). Files are read through
``intent_validator._read_target_file_for_review`` (deadline, secret-like path,
size cap, stored-secret-file gating). The rendered section is part of the
Stage 2 prompt, so the whole-prompt secret masking at
``_call_stage2_validation`` applies to it like to every other section.
"""

import ast
import builtins
import copy
import keyword
import os
import re
import textwrap
import time
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from .intent_validator import _read_target_file_for_review
from .logger import log_warning

STAGE2_SIGNATURES_MAX_CHARS = 1500
STAGE2_SIGNATURES_MAX_ENTRIES = 12
# A module (or the edited file) larger than this is not read for signatures:
# parsing a 2 MB file would cost seconds inside the PreToolUse deadline.
STAGE2_SIGNATURES_MAX_MODULE_BYTES = 300_000
_MAX_CANDIDATES = 40
_MAX_MODULES_READ = 8
_MAX_SCANNED_CODE_CHARS = 100_000
_MAX_ENTRY_CHARS = 300
_MAX_DOC_CHARS = 100
_OMITTED_NOTE_RESERVE = 40
_MAX_ROOT_WALK = 12
_PROJECT_MARKERS = (".git", "pyproject.toml", "setup.py", "setup.cfg")
_PATCH_FUNC_NAMES = frozenset({"patch", "object", "setattr"})
_SELF_RECEIVERS = frozenset({"self", "cls"})
_IGNORED_NAMES = frozenset(dir(builtins)) | frozenset(keyword.kwlist)
_HEADER_FILE = os.path.join(
    os.path.dirname(__file__),
    "prompts",
    "pre_tool_use",
    "stage2_called_signatures_header.md",
)

_IDENT_PATH = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
# Call scan for code that does not parse, kept LINEAR on purpose (#166 review
# H1): a maximal identifier/dotted run that does not start mid-token, then one
# O(1)-per-token check for "(". The earlier single pattern with nested
# quantifiers backtracked quadratically (30 s on 50k chars). Possessive
# quantifiers would also fix it but need Python 3.11; the hooks support 3.9.
_CHAIN_RE = re.compile(r"(?<![\w.])[A-Za-z_][\w.]*")
_OPEN_PAREN_RE = re.compile(r"\s*\(")
_DEFINED_RE = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")
_PATCH_STRING_RE = re.compile(
    r"""\b(?:patch|setattr)(?:\.\w+)?\(\s*(?:[A-Za-z_][\w.]*\s*,\s*)?"""
    r"""["']([A-Za-z_][\w.]*)["']"""
)


class Candidate(NamedTuple):
    """One called name. ``base`` is the dotted receiver of an attribute call
    (``"u"`` for ``u.compute()``), ``""`` for a receiver that is not a plain
    dotted name, ``None`` for a bare call. ``dotted`` marks a patch-target
    string such as ``"pkg.mod.func"`` (``name`` is then the whole path)."""

    base: Optional[str]
    name: str
    dotted: bool = False


def _expired(deadline: Optional[float]) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _parse(source: str) -> Optional[ast.Module]:
    try:
        return ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError, RecursionError):
        return None


def _dotted_name(node: ast.AST) -> Optional[str]:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _patch_candidates(call: ast.Call) -> List[Candidate]:
    found = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if _IDENT_PATH.match(arg.value):
                found.append(Candidate(None, arg.value, "." in arg.value))
    return found


def _candidates_from_tree(tree: ast.AST) -> "tuple[list, set]":
    positioned: List[Tuple[int, int, Candidate]] = []
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                cand, called = Candidate(None, func.id), func.id
            elif isinstance(func, ast.Attribute):
                base = _dotted_name(func.value)
                cand, called = Candidate(base or "", func.attr), func.attr
            else:
                continue
            at = (node.lineno, node.col_offset)
            positioned.append((at[0], at[1], cand))
            if called in _PATCH_FUNC_NAMES:
                positioned += [(at[0], at[1], c) for c in _patch_candidates(node)]
    positioned.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in positioned], defined


def _candidates_from_regex(code: str) -> "tuple[list, set]":
    defined = set(_DEFINED_RE.findall(code))
    found = []
    for match in _CHAIN_RE.finditer(code):
        chain = match.group(0)
        if not _OPEN_PAREN_RE.match(code, match.end()):
            continue
        if not _IDENT_PATH.match(chain):
            continue
        base, _, name = chain.rpartition(".")
        found.append(Candidate(base if base else None, name))
    for target in _PATCH_STRING_RE.findall(code):
        found.append(Candidate(None, target, "." in target))
    return found, defined


def _extract_candidates(code: str) -> List[Candidate]:
    """Called names in ``code`` (source order, de-duplicated, ignoring
    builtins and names the code itself defines)."""
    tree = _parse(code)
    raw, defined = (
        _candidates_from_tree(tree)
        if tree is not None
        else _candidates_from_regex(code)
    )
    kept: List[Candidate] = []
    for cand in raw:
        last = cand.name.rsplit(".", 1)[-1]
        if last in defined or last in _IGNORED_NAMES or cand in kept:
            continue
        kept.append(cand)
        if len(kept) >= _MAX_CANDIDATES:
            break
    return kept


class _Imports(NamedTuple):
    """Names an import statement binds: ``from_names`` maps a bound name to
    ``(module, level, original name)``; ``module_aliases`` maps a bound
    alias (or a dotted ``import a.b`` path) to the dotted module."""

    from_names: Dict[str, Tuple[str, int, str]]
    module_aliases: Dict[str, str]


_NO_IMPORTS = _Imports({}, {})
_FROM_RE = re.compile(
    r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+([^\n#(]+)", re.M
)
_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([^\n#]+)", re.M)
_DEF_LINE_RE = re.compile(
    r"^([ \t]*)((?:async[ \t]+)?def|class)[ \t]+([A-Za-z_]\w*)[^\n]*", re.M
)
# A quoted literal (an unterminated one runs to the end). Only ever applied to
# a def line already cut to _MAX_ENTRY_CHARS, so its cost is bounded.
_QUOTED_RE = re.compile(r"""'(?:\\.|[^'\\])*(?:'|$)|"(?:\\.|[^"\\])*(?:"|$)""")
# A triple-quoted literal matches as several adjacent quote pairs: collapse the
# resulting run of placeholders into one.
_PLACEHOLDER_RUN_RE = re.compile(r"(?:'\.\.\.'){2,}")


def _alias_pairs(text: str) -> "list[tuple[str, Optional[str]]]":
    pairs: List[Tuple[str, Optional[str]]] = []
    for part in text.split(","):
        words = part.split()
        if len(words) == 3 and words[1] == "as":
            pairs.append((words[0], words[2]))
        elif len(words) == 1:
            pairs.append((words[0], None))
    return pairs


def _bind_module(aliases: dict, name: str, asname: Optional[str]) -> None:
    aliases[asname or name] = name


def _scan_imports(source: str, tree: Optional[ast.AST]) -> _Imports:
    """Imports of ``source``: from ``tree`` when it parsed, else (parse
    failure) from a single-line regex scan."""
    from_names: Dict[str, Tuple[str, int, str]] = {}
    aliases: Dict[str, str] = {}
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        from_names[alias.asname or alias.name] = (
                            node.module or "",
                            node.level,
                            alias.name,
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    _bind_module(aliases, alias.name, alias.asname)
    else:
        for m in _FROM_RE.finditer(source):
            for name, asname in _alias_pairs(m.group(3)):
                from_names[asname or name] = (m.group(2), len(m.group(1)), name)
        for m in _IMPORT_RE.finditer(source):
            for name, asname in _alias_pairs(m.group(1)):
                _bind_module(aliases, name, asname)
    return _Imports(from_names, aliases)


def _merge_imports(first: _Imports, second: _Imports) -> _Imports:
    return _Imports(
        {**first.from_names, **second.from_names},
        {**first.module_aliases, **second.module_aliases},
    )


def _project_roots(file_path: str) -> "tuple[str, list[str]]":
    """``(project_root, source_roots)``: the nearest ancestor holding a
    project marker (bounded walk) and, for every directory up to it, the
    directory itself and its ``src`` child. Without a marker only the
    file's own directory counts."""
    directory = os.path.dirname(os.path.abspath(file_path))
    walk: List[str] = []
    current, root = directory, None
    for _ in range(_MAX_ROOT_WALK):
        walk.append(current)
        if any(os.path.exists(os.path.join(current, m)) for m in _PROJECT_MARKERS):
            root = current
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if root is None:
        root, walk = directory, [directory]
    candidates = [p for d in walk for p in (d, os.path.join(d, "src"))]
    return root, [p for p in dict.fromkeys(candidates) if os.path.isdir(p)]


def _module_file(
    module: str, level: int, importer: str, roots: List[str], root: str
) -> Optional[str]:
    """The ``.py`` / ``__init__.py`` file of a dotted module inside the
    project, or None (stdlib, third-party and out-of-project modules never
    resolve). ``level > 0`` is a relative import from ``importer``."""
    parts = [p for p in module.split(".") if p]
    bases = roots
    if level > 0:
        base = os.path.dirname(importer)
        for _ in range(level - 1):
            base = os.path.dirname(base)
        bases = [base]
    real_root = os.path.realpath(root) + os.sep
    for base in bases:
        stem = os.path.join(base, *parts)
        for path in ([stem + ".py"] if parts else []) + [
            os.path.join(stem, "__init__.py")
        ]:
            if os.path.isfile(path) and os.path.realpath(path).startswith(real_root):
                return path
    return None


class _Def(NamedTuple):
    path: str
    text: str
    doc: str
    owner: str


_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _first_doc_line(
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef],
) -> str:
    doc = (ast.get_docstring(node) or "").strip()
    line = doc.splitlines()[0].strip() if doc else ""
    return line if len(line) <= _MAX_DOC_CHARS else ""


def _blank_string_constants(roots: Sequence[ast.AST]) -> None:
    """Replace, in place, every string/bytes constant under ``roots`` with
    ``"..."``. Stored-secret masking cannot catch a credential hardcoded in
    source, so such values are never copied into the prompt."""
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                node.value = "..."


def _redacted_args(args: ast.arguments) -> str:
    """``args`` as source with every string/bytes default value replaced by
    ``'...'``: the argument names and order are what matters."""
    args = copy.deepcopy(args)
    _blank_string_constants(
        list(args.defaults) + [d for d in args.kw_defaults if d is not None]
    )
    return ast.unparse(args)


def _function_text(node) -> str:
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    text = f"{prefix}{node.name}({_redacted_args(node.args)})"
    if node.returns is not None:
        text += f" -> {ast.unparse(node.returns)}"
    return text


def _class_text(node: ast.ClassDef) -> str:
    parts = copy.deepcopy(list(node.bases) + list(node.keywords))
    _blank_string_constants(parts)  # class api_key='sk-...' / Base('x') literals
    bases = [ast.unparse(p) for p in parts]
    text = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    for item in node.body:
        if isinstance(item, _FUNCTION_NODES) and item.name == "__init__":
            return f"{text} -- {_function_text(item)}"
    return text


class _Module(NamedTuple):
    defs: Dict[str, _Def]
    methods: Dict[str, _Def]
    imports: _Imports


def _collect_methods(path: str, node: ast.ClassDef, methods: Dict[str, _Def]) -> None:
    for item in node.body:
        if isinstance(item, _FUNCTION_NODES):
            methods.setdefault(
                item.name,
                _Def(path, _function_text(item), _first_doc_line(item), node.name),
            )


def _parse_module(path: str, source: str) -> _Module:
    tree = _parse(source)
    defs: Dict[str, _Def] = {}
    methods: Dict[str, _Def] = {}
    if tree is None:
        for m in _DEF_LINE_RE.finditer(source):
            target = methods if m.group(1) else defs
            line = m.group(0).strip().rstrip(":")[:_MAX_ENTRY_CHARS]
            text = _PLACEHOLDER_RUN_RE.sub("'...'", _QUOTED_RE.sub("'...'", line))
            target.setdefault(m.group(3), _Def(path, text, "", ""))
    else:
        for node in tree.body:
            if isinstance(node, _FUNCTION_NODES):
                text = _function_text(node)
            elif isinstance(node, ast.ClassDef):
                text = _class_text(node)
                _collect_methods(path, node, methods)
            else:
                continue
            defs[node.name] = _Def(path, text, _first_doc_line(node), "")
    return _Module(defs, methods, _scan_imports(source, tree))


class _Resolver:
    """Maps called names to definitions. Every module is read and parsed at
    most once, through ``_read_target_file_for_review``."""

    def __init__(
        self,
        file_path: str,
        deadline: Optional[float],
        db_path,
        target_content: Optional[str] = None,
    ):
        self.file_path = file_path
        self.deadline = deadline
        self.db_path = db_path
        self.root, self.roots = _project_roots(file_path)
        self.cache: Dict[str, Optional[_Module]] = {}
        self.imports = _NO_IMPORTS
        if target_content is not None:  # the caller already read the edited file
            oversized = len(target_content) > STAGE2_SIGNATURES_MAX_MODULE_BYTES
            self.cache[file_path] = (
                None if oversized else _parse_module(file_path, target_content)
            )

    def module(self, path: str) -> Optional[_Module]:
        if path in self.cache:
            return self.cache[path]
        if len(self.cache) >= _MAX_MODULES_READ:
            return None
        try:
            if os.path.getsize(path) > STAGE2_SIGNATURES_MAX_MODULE_BYTES:
                self.cache[path] = None
                return None
        except OSError:
            pass  # missing/unreadable: the shared reader below reports it
        content, _note = _read_target_file_for_review(
            path, _deadline=self.deadline, _db_path=self.db_path
        )
        self.cache[path] = None if content is None else _parse_module(path, content)
        return self.cache[path]

    def _file(self, module: str, level: int, importer: str) -> Optional[str]:
        return _module_file(module, level, importer, self.roots, self.root)

    def lookup(self, module_path: str, name: str, hops: int = 1) -> Optional[_Def]:
        """``name`` as defined in ``module_path``; when the module only
        re-exports it (``from .logger import log_warning``), follow that
        import for up to ``hops`` more modules."""
        info = self.module(module_path)
        if info is None:
            return None
        found = info.defs.get(name)
        if found or hops <= 0 or name not in info.imports.from_names:
            return found
        mod, level, original = info.imports.from_names[name]
        target = self._file(mod, level, module_path)
        return self.lookup(target, original, hops - 1) if target else None

    def same_file(self, name: str) -> Optional[_Def]:
        info = self.module(self.file_path)
        return info.defs.get(name) if info else None

    def own_method(self, name: str) -> Optional[_Def]:
        """A method of a class in the edited file, by name: only meaningful
        for a ``self``/``cls`` receiver, never for an arbitrary object."""
        info = self.module(self.file_path)
        return info.methods.get(name) if info else None

    def module_for_base(self, base: str) -> Optional[str]:
        """Project file of the module an attribute call's receiver names."""
        imports = self.imports
        if base in imports.from_names:  # `from pkg import util` binds a module
            mod, level, original = imports.from_names[base]
            return self._file(
                f"{mod}.{original}" if mod else original, level, self.file_path
            )
        for key, module in imports.module_aliases.items():
            if base == key or base.startswith(key + "."):
                return self._file(module + base[len(key) :], 0, self.file_path)
        return None

    def _resolve_dotted(self, dotted: str) -> Optional[_Def]:
        """A patch-target string ``pkg.mod.func``: longest module prefix
        that is a project file and defines (or re-exports) the last part."""
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            path = self._file(".".join(parts[:i]), 0, self.file_path)
            found = self.lookup(path, parts[i]) if path else None
            if found:
                return found
        return None

    def resolve(self, cand: Candidate) -> Optional[_Def]:
        if cand.dotted:
            return self._resolve_dotted(cand.name)
        if cand.base is None:
            if cand.name in self.imports.from_names:
                mod, level, original = self.imports.from_names[cand.name]
                path = self._file(mod, level, self.file_path)
                return self.lookup(path, original) if path else None
            return self.same_file(cand.name)
        if cand.base in _SELF_RECEIVERS:
            return self.own_method(cand.name)
        # Any other receiver is an object of unknown type (d.get, cfg.update,
        # ', '.join): only a project MODULE receiver can be resolved.
        path = self.module_for_base(cand.base)
        return self.lookup(path, cand.name) if path else None


def _entry_text(found: _Def, root: str, show_doc: bool) -> str:
    """One entry line. ``show_doc`` is true only for the edited file itself:
    its content is already in the prompt, whereas another module's docstring
    would be extra, unmasked text that adds no argument-order information."""
    rel = os.path.relpath(found.path, root).replace(os.sep, "/")
    notes = [f"method of {found.owner}"] if found.owner else []
    if found.doc and show_doc:
        notes.append(found.doc)
    entry = f"- {rel}: {found.text}" + (f"  # {'; '.join(notes)}" if notes else "")
    if len(entry) > _MAX_ENTRY_CHARS:
        entry = entry[: _MAX_ENTRY_CHARS - 3] + "..."
    return entry


def _render_section(entries: List[str]) -> str:
    """Header plus as many entries as the caps allow; the whole section stays
    within ``STAGE2_SIGNATURES_MAX_CHARS``."""
    with open(_HEADER_FILE, "r", encoding="utf-8") as header_file:
        prefix = "\n" + header_file.read().strip() + "\n"
    used = len(prefix)
    shown: List[str] = []
    for entry in entries:
        if len(shown) >= STAGE2_SIGNATURES_MAX_ENTRIES:
            break
        if used + len(entry) + 1 > STAGE2_SIGNATURES_MAX_CHARS - _OMITTED_NOTE_RESERVE:
            break
        shown.append(entry)
        used += len(entry) + 1
    if not shown:
        return ""
    omitted = len(entries) - len(shown)
    note = f"...[{omitted} more omitted]\n" if omitted else ""
    return prefix + "\n".join(shown) + "\n" + note


def _build_section(file_path, new_code, deadline, db_path, target_content) -> str:
    if not isinstance(file_path, str) or not isinstance(new_code, str):
        return ""
    if not file_path.endswith(".py") or not new_code.strip() or _expired(deadline):
        return ""
    code = new_code[:_MAX_SCANNED_CODE_CHARS]
    candidates = _extract_candidates(code)
    if not candidates:
        return ""
    resolver = _Resolver(file_path, deadline, db_path, target_content)
    on_disk = resolver.module(file_path)
    resolver.imports = _merge_imports(
        on_disk.imports if on_disk else _NO_IMPORTS,
        _scan_imports(code, _parse(code)),
    )
    entries: List[str] = []
    seen = set()
    for cand in candidates:
        if _expired(deadline):
            return ""
        found = resolver.resolve(cand)
        if found is None or (found.path, found.text) in seen:
            continue
        seen.add((found.path, found.text))
        entries.append(_entry_text(found, resolver.root, found.path == file_path))
        if len(entries) > STAGE2_SIGNATURES_MAX_ENTRIES:
            break
    if not entries or _expired(deadline):
        return ""
    return _render_section(entries)


def build_called_signatures_section(
    file_path: str,
    new_code: str,
    _deadline: Optional[float] = None,
    _db_path: Optional[str] = None,
    _target_content: Optional[str] = None,
) -> str:
    """The Stage 2 "SIGNATURES OF CALLED FUNCTIONS" section for ``new_code``
    (the added/new code of a Write/Edit on ``file_path``), or ``""``: not a
    Python file, nothing resolvable, past ``_deadline`` (a
    ``time.monotonic()`` value) or any error (logged as a warning, never
    raised, so Stage 2 itself is never blocked by this section).

    ``_target_content``: the edited file's content when the caller already
    has it (the hook's read for the diff and surrounding context; for a
    Write, the new content). It is used instead of reading the file again;
    ``None`` means read it from disk."""
    try:
        return _build_section(file_path, new_code, _deadline, _db_path, _target_content)
    except Exception as exc:  # fail-safe by contract (#166)
        log_warning("stage2_signatures", "Called-signatures section skipped", exc)
        return ""
