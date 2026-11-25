"""Shared test helpers: write migration scripts into a temp dir."""

import sys
import tempfile
from pathlib import Path

APPWRITE_DIR = Path(__file__).resolve().parent.parent
if str(APPWRITE_DIR) not in sys.path:
    sys.path.insert(0, str(APPWRITE_DIR))


def make_dir():
    tmp = tempfile.TemporaryDirectory()
    return tmp, Path(tmp.name)


def script(directory, name, body="ctx.client.log.append('{name}')"):
    """Write migrations/<name>.py with an `up(ctx)` that runs `body`."""
    text = f"def up(ctx):\n    {body.format(name=name)}\n"
    (Path(directory) / f"{name}.py").write_text(text)
