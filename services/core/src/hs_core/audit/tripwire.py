"""AST TRIPWIRES for the audit choke point (M06 #41). They are tripwires, not coverage.

`find_unaudited_writes(root)`: flags a call to create/update/delete (and the SDK write-style
methods in append_only.WRITE_METHODS) whose argument is a LITERAL audited table name (or a name
bound anywhere in the same file to one), when the receiver expression does not contain
"audited" (the convention: variables holding an AuditedRepository are named `audited...`).

`find_unaudited_case_reads(root)`: flags get/list/get_row/list_rows with a literal
`safeguarding_case`/`safeguarding_action` argument anywhere outside the audit package.

They MISS (documented in docs/ops/audit.md; tests/test_audit_reads.py keeps the list honest):
table names that are computed (f-strings, concatenation), passed as a function parameter, held in
a class attribute or container constant, supplied through *args/**kwargs splats, reached via
getattr or a bound-method alias, or dict dispatch; a raw repository stored in a variable whose
name contains "audited"; and writes made with the raw repository from code the scan is not run
over. The runtime checks (AuditedRepository refusing context-free writes and plain case reads)
only protect code that holds the wrapper.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..append_only import WRITE_METHODS, _string_constants
from .registry import AUDITED_TABLES, CASE_TABLES

_WRITES = WRITE_METHODS | {"create"}
_READS = frozenset({"get", "list", "get_row", "list_rows"})
_EXEMPT_FILES = frozenset({"repository.py", "append_only.py"})
# C03: the retention hold check must know whether a case is OPEN. It reads ids and status only
# (`Query.select`, never the description or outcome); a test (test_retention_tripwire) pins this.
CASE_READ_EXEMPT = frozenset({"retention/holds.py"})


def _is_exempt_dir(path: Path, root: Path) -> bool:
    return "audit" in path.relative_to(root).parts[:-1]


def _table_args(node: ast.Call, consts: dict[str, str]) -> list[str]:
    out = []
    for arg in [*node.args, *(k.value for k in node.keywords)]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.append(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in consts:
            out.append(consts[arg.id])
    return out


def _calls(root: Path, skip_files: frozenset[str], skip_paths: frozenset[str] = frozenset()):
    root = Path(root)
    for path in sorted(root.rglob("*.py")):
        if path.name in skip_files or _is_exempt_dir(path, root):
            continue
        if path.relative_to(root).as_posix() in skip_paths:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        consts = _string_constants(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                yield path, n, consts


def find_unaudited_writes(root: Path) -> list[str]:
    found = []
    for path, n, consts in _calls(root, _EXEMPT_FILES):
        if n.func.attr not in _WRITES:
            continue
        hit = [t for t in _table_args(n, consts) if t in AUDITED_TABLES]
        if hit and "audited" not in ast.unparse(n.func.value).lower():
            found.append(
                f"{path}:{n.lineno}: {n.func.attr}() on audited table {hit[0]} outside an "
                "AuditedRepository (use the `audited` repository with an AuditContext)"
            )
    return found


def find_unaudited_case_reads(root: Path) -> list[str]:
    found = []
    for path, n, consts in _calls(root, frozenset(), CASE_READ_EXEMPT):
        if n.func.attr not in _READS:
            continue
        hit = [t for t in _table_args(n, consts) if t in CASE_TABLES]
        if hit:
            found.append(
                f"{path}:{n.lineno}: {n.func.attr}() reads {hit[0]} outside "
                "read_audited/list_audited"
            )
    return found
