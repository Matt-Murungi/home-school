"""docs/ops/permissions.md: the matrix is GENERATED from `rules.SPECS` so the documentation
cannot drift from the code (a test regenerates it and compares). Run
`uv run python scripts/permissions_check.py --write-docs` after changing a rule."""

from __future__ import annotations

from pathlib import Path

from .policy import LABELS
from .rules import (
    CLIENT_WRITABLE_TABLES,
    FILE_BUCKETS,
    PENDING_SERVICE_ONLY,
    SERVICE_ONLY_TABLES,
    SPECS,
    WRITE_PROTECTED,
)

BEGIN = (
    "<!-- BEGIN GENERATED: matrix (do not edit; run scripts/permissions_check.py --write-docs) -->"
)
END = "<!-- END GENERATED: matrix -->"
DOC = Path(__file__).resolve().parents[5] / "docs" / "ops" / "permissions.md"

COLUMNS = (
    ("parent", "Parent / guardian"),
    ("teacher", "Teacher"),
    ("ops", "Ops admin"),
    ("curator", "Curator"),
    ("lead", "Safeguarding lead"),
    ("superadmin", "Super admin"),
)
LABEL_COL = {
    "ops": "opsadmin",
    "curator": "curator",
    "lead": "safeguardinglead",
    "superadmin": "superadmin",
}


def _cell(table: str, key: str) -> str:
    spec = SPECS[table]
    text = spec.audience.get(key, "")
    if key == "parent":
        bits = [spec.audience.get(k, "") for k in ("parent", "self", "reporter", "everyone")]
        bits = [b for b in bits if b]
        if spec.audience.get("guardian"):
            bits.append(spec.audience["guardian"])
        return "; ".join(bits) or "no"
    if key in LABEL_COL:
        if LABEL_COL[key] in spec.static_labels():
            return "read (label)"
        return text or "no"
    return text or "no"


def render_matrix() -> str:
    lines = [BEGIN, ""]
    lines.append("### Who reads which table")
    lines.append("")
    head = ["Table", *[c[1] for c in COLUMNS]]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "|".join(["---"] * len(head)) + "|")
    for table in sorted(SPECS):
        spec = SPECS[table]
        if spec.service_only:
            continue
        cells = [f"`{table}`", *[_cell(table, k).replace("|", "/") for k, _ in COLUMNS]]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "### Service-only tables (no client read for anyone)", ""]
    for table, why in sorted({**SERVICE_ONLY_TABLES, **PENDING_SERVICE_ONLY}.items()):
        pending = " (table arrives with its own ticket)" if table in PENDING_SERVICE_ONLY else ""
        lines.append(f"- `{table}`: {why}{pending}")
    lines += ["", "### Buckets (file permission = the owning row's permission)", ""]
    for bucket, (table, cols) in sorted(FILE_BUCKETS.items()):
        lines.append(f"- `{bucket}`: files named by `{table}.{'`, `'.join(cols)}`")
    lines += ["", "### Data clients can never write (core service only)", ""]
    for what, tables in WRITE_PROTECTED.items():
        lines.append(f"- {what}: " + ", ".join(f"`{t}`" for t in tables))
    lines += [
        "",
        f"Tables with any client create/update/delete: {sorted(CLIENT_WRITABLE_TABLES) or 'none'}.",
        "",
        "Labels: " + ", ".join(f"`{x}`" for x in LABELS) + ".",
        "",
        END,
    ]
    return "\n".join(lines)


def current_block(text: str) -> str | None:
    if BEGIN not in text or END not in text:
        return None
    return text[text.index(BEGIN) : text.index(END) + len(END)]


def write_doc(path: Path = DOC) -> bool:
    """Replace the generated block. Returns True when the file changed."""
    text = path.read_text()
    block = current_block(text)
    if block is None:
        raise ValueError("generated block markers missing in " + str(path))
    new = text.replace(block, render_matrix())
    if new != text:
        path.write_text(new)
    return new != text


def doc_is_current(path: Path = DOC) -> bool:
    block = current_block(path.read_text())
    return block == render_matrix()
