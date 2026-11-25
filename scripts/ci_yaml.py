"""Tiny YAML-subset reader for the workflow files (stdlib only, F07).

Supports what `.github/workflows/*.yml` and `dependabot.yml` use here: block mappings and sequences,
`key: value` scalars (always returned as str, quotes removed), flow lists `[a, b]`, empty flow
mapping `{}`, block scalars (`|`, `|-`, `>`), and comments. Anchors, tags and multi-line plain or
quoted scalars are NOT supported and raise ValueError, so a workflow that needs them fails loudly
instead of being half-read. The tests cross-check this reader against PyYAML when `uv` is available.
"""

from __future__ import annotations

import re

KEY_RE = re.compile(
    r"""^(?P<key>"[^"]*"|'[^']*'|[^\s:#'"\[\]{},&*!|>%@`-][^:]*?|-[^\s:][^:]*?):(?:\s+(?P<val>.*))?$"""
)


def _strip_comment(text: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            if i == 0 or text[i - 1] in " \t:[,{-":
                quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(text: str):
    text = text.strip()
    if text == "":
        return ""
    if text[0] in "&*!":
        raise ValueError(f"unsupported YAML (anchor/alias/tag): {text!r}")
    if text[0] == '"' and text.endswith('"') and len(text) > 1:
        return text[1:-1].replace('\\"', '"')
    if text[0] == "'" and text.endswith("'") and len(text) > 1:
        return text[1:-1].replace("''", "'")
    if text == "{}":
        return {}
    if text[0] == "[":
        if not text.endswith("]"):
            raise ValueError(f"unterminated flow list: {text!r}")
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(p) for p in _split_flow(inner)]
    return text


def _split_flow(inner: str) -> list[str]:
    parts, cur, quote, depth = [], [], None, 0
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == "[":
            depth += 1
            cur.append(ch)
        elif ch == "]":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


class _Lines:
    def __init__(self, text: str):
        self.raw = text.expandtabs(8).splitlines()
        self.i = 0

    def peek(self):
        """Next significant line as (indent, content, raw_index) or None."""
        j = self.i
        while j < len(self.raw):
            line = self.raw[j]
            stripped = _strip_comment(line)
            if stripped.strip() == "" or stripped.strip() == "---":
                j += 1
                continue
            return len(line) - len(line.lstrip(" ")), stripped.strip(), j
        return None


def _block_scalar(lines: _Lines, header: str, parent_indent: int) -> str:
    style = header[0]
    chomp = "-" if "-" in header else ("+" if "+" in header else "")
    body, indent = [], None
    while lines.i < len(lines.raw):
        line = lines.raw[lines.i]
        if line.strip() == "":
            body.append("")
            lines.i += 1
            continue
        cur = len(line) - len(line.lstrip(" "))
        if cur <= parent_indent:
            break
        if indent is None:
            indent = cur
        body.append(line[indent:] if cur >= indent else line.lstrip(" "))
        lines.i += 1
    while body and body[-1] == "" and chomp != "+":
        body.pop()
    text = (
        "\n".join(body) if style == "|" else " ".join(b for b in body if b != "")
    ) + ("" if chomp == "-" or not body else "\n")
    return text


def _parse_block(lines: _Lines, indent: int):
    nxt = lines.peek()
    if nxt is None or nxt[0] < indent:
        return None
    cur_indent, content, _ = nxt
    if content.startswith("- ") or content == "-":
        return _parse_seq(lines, cur_indent)
    return _parse_map(lines, cur_indent)


def _parse_value(lines: _Lines, val: str | None, indent: int):
    """Value after `key:` or `- `; may continue on following more-indented lines."""
    if val is None or val == "":
        nxt = lines.peek()
        if nxt and (nxt[0] > indent or nxt[1].startswith("- ")) and nxt[0] >= indent:
            return _parse_block(lines, nxt[0])
        return ""
    if val[0] in "|>":
        return _block_scalar(lines, val, indent)
    if val[0] in "&*!":
        raise ValueError(f"unsupported YAML (anchor/alias/tag): {val!r}")
    if val[0] in "\"'" and not (len(val) > 1 and val.endswith(val[0])):
        raise ValueError(f"multi-line quoted scalar unsupported: {val!r}")
    if val[0] == "[" and not val.endswith("]"):
        raise ValueError(f"multi-line flow list unsupported: {val!r}")
    return _scalar(val)


def _parse_map(lines: _Lines, indent: int) -> dict:
    out: dict = {}
    while True:
        nxt = lines.peek()
        if nxt is None or nxt[0] != indent or nxt[1].startswith("- "):
            if nxt is not None and nxt[0] > indent:
                raise ValueError(f"line {nxt[2] + 1}: unexpected indent")
            return out
        _, content, j = nxt
        m = KEY_RE.match(content)
        if not m:
            raise ValueError(f"line {j + 1}: cannot parse {content!r}")
        key = _scalar(m.group("key"))
        lines.i = j + 1
        out[key] = _parse_value(lines, m.group("val"), indent)


def _parse_seq(lines: _Lines, indent: int) -> list:
    out: list = []
    while True:
        nxt = lines.peek()
        if (
            nxt is None
            or nxt[0] != indent
            or not (nxt[1].startswith("- ") or nxt[1] == "-")
        ):
            return out
        _, content, j = nxt
        rest = content[1:].strip()
        if rest == "":
            lines.i = j + 1
            out.append(_parse_value(lines, "", indent))
            continue
        m = KEY_RE.match(rest)
        if (
            m
            and not rest.startswith(("[", "{", '"', "'"))
            or (m and rest[0] in "\"'" and m.group("key") == rest.split(":")[0])
        ):
            # inline mapping start: re-indent the remainder as a mapping at indent+2
            offset = len(content) - len(rest)
            lines.raw[j] = " " * (indent + offset) + rest
            lines.i = j
            out.append(_parse_map(lines, indent + offset))
        else:
            lines.i = j + 1
            if rest[0] in "|>":
                out.append(_block_scalar(lines, rest, indent))
            else:
                out.append(_parse_value(lines, rest, indent))


def load(text: str):
    lines = _Lines(text)
    nxt = lines.peek()
    if nxt is None:
        return {}
    doc = _parse_block(lines, nxt[0])
    if lines.peek() is not None:
        raise ValueError(f"line {lines.peek()[2] + 1}: trailing content not parsed")
    return doc
