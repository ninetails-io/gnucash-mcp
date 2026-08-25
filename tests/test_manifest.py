"""Contract lock: manifest.json ↔ the server it launches.

The MCPB manifest names env vars, CLI args, and a version by string;
nothing at runtime would notice them drifting from the server's
actual interface (a renamed toggle would silently strand a checkbox;
a missed version bump would ship a bundle self-identifying as the
previous release — the v1.4.1 stale-lockfile class). These tests are
the drift alarm, and the same suite runs in CI before every pack.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_MANIFEST = _ROOT / "manifest.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(_MANIFEST.read_text())


class TestManifestContract:
    def test_version_matches_package(self, manifest):
        from gnucash_mcp import __version__
        assert manifest["version"] == __version__

    def test_version_matches_pyproject(self, manifest):
        text = (_ROOT / "pyproject.toml").read_text()
        match = re.search(r'^version = "([^"]+)"', text, re.M)
        assert match is not None
        assert manifest["version"] == match.group(1)

    def test_env_keys_cover_the_server_interface(self, manifest):
        """Every env var the server's bundle paths read must be set
        by the manifest — a server-side rename may not leave the
        manifest feeding a dead variable."""
        from gnucash_mcp.server import _ENV_MODULE_TOGGLES
        env = manifest["server"]["mcp_config"]["env"]
        expected = set(_ENV_MODULE_TOGGLES) | {
            "GNUCASH_DEMO_BOOKS",
            "GNUCASH_DEMO_DIR",
            "GNUCASH_MCP_ADVANCED",
            "GNUCASH_REDACT_PATHS",
        }
        missing = expected - set(env)
        assert not missing, f"manifest env missing: {sorted(missing)}"

    def test_placeholders_reference_declared_user_config(self, manifest):
        """${user_config.X} placeholders must name declared fields,
        and the book picker must actually feed --book."""
        declared = set(manifest["user_config"])
        blob = json.dumps(manifest["server"]["mcp_config"])
        used = set(re.findall(r"\$\{user_config\.([a-z_]+)\}", blob))
        undeclared = used - declared
        assert not undeclared, f"placeholders without fields: {undeclared}"
        unused = declared - used
        assert not unused, f"fields feeding nothing: {unused}"
        args = manifest["server"]["mcp_config"]["args"]
        assert "--book" in args
        assert args[args.index("--book") + 1] == "${user_config.books}"

    def test_demo_dir_points_inside_the_bundle(self, manifest):
        env = manifest["server"]["mcp_config"]["env"]
        assert env["GNUCASH_DEMO_DIR"] == "${__dirname}/samples"

    def test_mcpbignore_negations_name_real_books(self):
        """Each !samples/... re-inclusion must exist on disk, or the
        pack silently ships fewer demo books than intended."""
        negations = [
            line[1:].strip()
            for line in (_ROOT / ".mcpbignore").read_text().splitlines()
            if line.startswith("!")
        ]
        assert negations, "expected demo-book negations in .mcpbignore"
        for rel in negations:
            assert (_ROOT / rel).is_file(), f"negated but missing: {rel}"
