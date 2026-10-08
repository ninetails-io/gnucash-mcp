"""A book GnuCash itself created, made headless for one test run.

``make_book(path)`` has ``gnucash-cli`` load ``gnucash_new_book.scm``
and create a fresh SQLite book at ``path`` through the engine. The
book is never committed: it is built where ``gnucash-cli`` exists and
the tests that need it skip elsewhere. See the ``.scm`` for the chart.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "gnucash_new_book.scm"
sys.path.insert(0, str(HERE))
from engine_twin import find_gnucash_cli  # noqa: E402


def make_book(path: Path, host: Path) -> Path:
    """Create a GnuCash-made book at ``path``. ``host`` is any book
    ``gnucash-cli`` can open to run the report against; it is not
    changed in any way that matters to a test."""
    cli = find_gnucash_cli()
    if cli is None:
        raise RuntimeError("gnucash-cli not found (set GNUCASH_CLI)")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "data").mkdir()
        (tmp / "config").mkdir()
        (tmp / "config" / "config-user.scm").write_text(
            f'(load "{SCRIPT}")\n'
        )
        out = tmp / "report.html"
        env = dict(
            os.environ,
            GNC_DATA_HOME=str(tmp / "data"),
            GNC_CONFIG_HOME=str(tmp / "config"),
            GUILE_AUTO_COMPILE="0",
            NEW_BOOK=str(path),
        )
        run = subprocess.run(
            [cli, "--report", "run", "--name", "New Book",
             "--output-file", str(out), str(host)],
            env=env, capture_output=True, text=True, timeout=900,
        )
        made = re.findall(r"NEW\|(-?\d+)\|", out.read_text()) if out.exists() else []
    if run.returncode != 0 or made != ["0"] or not Path(path).exists():
        raise RuntimeError(
            f"gnucash-cli did not create the book ({run.returncode}, "
            f"{made}):\n{run.stdout[-1500:]}\n{run.stderr[-1500:]}"
        )
    return Path(path)
