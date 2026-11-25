"""docs/ops/settings.md: the table of settings is GENERATED from the registry, so the document
cannot drift from the code (tests/test_settings_registry.py compares them)."""

from __future__ import annotations

from pathlib import Path

from .model import SettingDef
from .registry import REGISTRY

BEGIN, END = "<!-- settings:begin -->", "<!-- settings:end -->"
DOC = Path(__file__).resolve().parents[5] / "docs" / "ops" / "settings.md"
APPLIES = {
    "immediate": "from its effective time (now unless scheduled)",
    "next_day": "from the next Africa/Kampala midnight (or later if scheduled)",
}


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _bounds(d: SettingDef) -> str:
    if d.kind == "map":
        parts = [f"`{f.name}` {f.lo} to {f.hi}" for f in d.fields]
        tail = f"; sum = {d.sum_to}" if d.sum_to is not None else ""
        return "; ".join(parts) + tail
    return f"{_n(d.lo)} to {_n(d.hi)}"


def _default(d: SettingDef) -> str:
    v = d.code_default()
    return ", ".join(f"{k}={x}" for k, x in v.items()) if isinstance(v, dict) else f"{v}"


def _env(d: SettingDef) -> str:
    if d.kind == "map":
        names = [f"`{f.env.upper()}`" for f in d.fields if f.env]
        return ", ".join(names) or "none"
    return f"`{d.env.upper()}`" if d.env else "none"


def render_table() -> str:
    titles = dict(REGISTRY.groups)
    out: list[str] = []
    for gid, title in REGISTRY.groups:
        defs = [d for d in REGISTRY.defs.values() if d.group == gid]
        out.append(f"### {title}\n")
        head = "| Key | Type | Allowed | Default | Unit | Environment default | Applies |"
        out.append(head + " Description |")
        out.append("|---|---|---|---|---|---|---|---|")
        for d in defs:
            out.append(
                f"| `{d.key}` | {d.kind} | {_bounds(d)} | {_default(d)} | {d.unit} | {_env(d)} | "
                f"{APPLIES[d.applies]} | {d.description} |"
            )
        out.append("")
    cons = ["### Rules across settings\n"]
    for c in REGISTRY.constraints:
        cons.append(f"- `{c.name}`: {c.message} (keys: {', '.join(f'`{k}`' for k in c.keys)})")
    out.append("\n".join(cons))
    assert titles
    return "\n".join(out).strip() + "\n"


def write_docs(path: Path | None = None) -> bool:
    """Rewrite the generated block; True when the file changed."""
    p = Path(path) if path else DOC
    text = p.read_text()
    head, rest = text.split(BEGIN, 1)
    _old, tail = rest.split(END, 1)
    new = f"{head}{BEGIN}\n{render_table()}{END}{tail}"
    if new == text:
        return False
    p.write_text(new)
    return True
