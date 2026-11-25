#!/usr/bin/env python3
"""Minimal secret scanner (no network, stdlib only). Complements gitleaks (.gitleaks.toml).

usage: secret_scan.py [--path DIR]   default: files tracked by git (plus untracked, not ignored)
Prints file:line and the rule name, never the matched value. Exit 1 if anything is found.
Allow a line with the marker `secret-scan: allow`.
"""
import re
import subprocess
import sys
from pathlib import Path

RULES = {
    "aws-access-key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "private-key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "appwrite-api-key": re.compile(r"\bstandard_[0-9a-f]{64,}\b"),
    "generic-assignment": re.compile(
        r"""(?ix)\b[\w.-]*(secret|api[_-]?key|token|password|passwd)[\w.-]*\s*[:=]\s*["']?([A-Za-z0-9_\-+/=.]{20,})["']?"""
    ),
}
PLACEHOLDER = re.compile(r"(?i)(your|example|changeme|placeholder|xxxx|<|\$\{|process\.env|os\.environ|getenv|redacted)")
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".lock", ".woff", ".woff2", ".zip"}
SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", ".pytest_cache", "build", ".dart_tool"}


def files(root: Path, tracked_only: bool):
    if tracked_only:
        try:
            out = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=root, capture_output=True, check=True,
            ).stdout.decode().split("\0")
            return [root / f for f in out if f and (root / f).is_file()]
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return [p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)]


def scan(root: Path, tracked_only=False):
    hits = []
    for f in files(root, tracked_only):
        if f.suffix in SKIP_SUFFIX or f.name == "package-lock.json" or f.name == "uv.lock":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if "secret-scan: allow" in line:
                continue
            for name, rx in RULES.items():
                m = rx.search(line)
                if not m:
                    continue
                if name == "generic-assignment" and PLACEHOLDER.search(line):
                    continue
                hits.append((f.relative_to(root), n, name))
    return hits


def main(argv):
    if "--path" in argv:
        root, tracked = Path(argv[argv.index("--path") + 1]), False
    else:
        root, tracked = Path(__file__).resolve().parents[1], True
    hits = scan(root, tracked)
    for f, n, name in hits:
        print(f"secret-scan: {f}:{n}: possible secret ({name}); value not shown")
    if not hits:
        print("secret-scan ok")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
