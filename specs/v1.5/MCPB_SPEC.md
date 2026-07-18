# MCPB Packaging Spec — One-Click Install for Non-Developers

Status: DRAFT (research complete 2026-07-16; implementation targeted
at v1.5). Research sources at the bottom.

## Why

The install process today requires a developer: clone, `uv sync`,
`GNUCASH_BOOK_PATH` with `os.pathsep`-separated paths, `--modules`
with a dozen options, JSON edits to `claude_desktop_config.json`.
An MCPB (`.mcpb`) bundle turns that into: download one file,
double-click, pick your book in a file dialog, toggle four
checkboxes.

## Research findings (spec v0.4, July 2026)

The spec lives at `modelcontextprotocol/mcpb` (migrated from
`anthropics/dxt`), current manifest version **0.4**.

1. **The Python runtime problem is solved by the spec itself.**
   `server.type: "uv"` (v0.4+, marked *experimental*): the host app
   (Claude Desktop) manages uv, downloads the correct Python for
   the user's platform, and installs dependencies from the bundle's
   `pyproject.toml`. No vendored `lib/`, no compiled-wheel matrix,
   no host Python required. This retires the pydantic-core concern
   entirely — uv resolves the right platform wheel at install time.
   - Fallback if the experimental type proves unstable: classic
     `type: "python"` with vendored deps is a non-starter
     (pydantic-core is platform × Python-minor specific); the
     realistic fallback is a Node launcher shim (Node IS bundled
     with Claude Desktop) that bootstraps uv. Documented here so we
     never re-litigate vendoring.
   - Known issue: a Windows venv-build race on first launch
     (anthropics/claude-code#38266). Watch status before ship.

2. **`user_config` capabilities** (unchanged since 0.1, still no
   enum/select/multi-select):
   - Types: `string` (with `sensitive`), `number` (min/max),
     `boolean`, `directory`, `file`.
   - `file`/`directory` support `multiple: true`. In `args`, a
     multiple value **expands to separate arguments**. Env-var
     expansion of multiple values is UNSPECIFIED — do not rely on
     it; always deliver multi-values through args.
   - `platform_overrides` in `mcp_config` allows per-OS
     command/args/env.

3. **Reference manifest shape** (from the official
   `hello-world-uv` example): `server.type: "uv"`,
   `mcp_config.command: "uv"`,
   `args: ["run", "--directory", "${__dirname}", "src/server.py"]`,
   `compatibility.runtimes.python: ">=3.10"`.

## Design

### Philosophy

The manifest exposes **who you are**, not what the server can do:
your book file(s) and four persona checkboxes. Every
other option gets an opinionated non-developer default. The CLI
remains the developer surface, unchanged.

### user_config

| Key | Type | Default | Maps to |
|---|---|---|---|
| `books` | `file`, `multiple: true`, required | — | repeated `--book` args (NEW server flag, see prerequisites) |
| `freelancer` | `boolean` | `false` | `freelancer` module group ("Client invoicing — send invoices, record payments") |
| `business` | `boolean` | `false` | `business` group alias = freelancer + business_complete ("Full business suite — adds vendors, bills, employee expenses") |
| `investments` | `boolean` | `false` | portfolio/investor groups ("Investment tracking") |
| `planning` | `boolean` | `true` | budgets + scheduling ("Budgets & scheduled transactions") |

Booleans are checkboxes — a checkbox list is just N booleans. Four
persona toggles, not twelve module names — matching the role-
aligned module split v1.3 built deliberately (freelancer is the
customer-invoicing surface; `business` is the group alias that
includes it and adds the vendor/employee side, per the PR #93
redistribution). `business: true` supersedes `freelancer`; checking
both is harmless. Core, reporting, and reconciliation are always
on.

### Tool budget

Measured against v1.4.1's `TOOL_MODULES` (107 total). Core (forced:
summary, accounts, transactions, slots, audit, backup,
balance_sheet, diagnostic, reconciliation) is 30 tools; reporting
(+5) is always on in this design.

| Configuration | Tools |
|---|---|
| Core + reporting (all toggles off) | 35 |
| + planning (default ON) | 47 |
| + investments | 59 |
| + freelancer | 66 |
| + full business suite | 83 |
| everything | 107 |

The bundle targets Claude Desktop, which is comfortable at 100+
tools, so the default ships at 47 (planning on) — we are NOT
designing to the ~40-tool caps some other connector clients impose;
those clients don't consume MCPBs. The counts are recorded here so
the trade-off stays visible: capped-client support is a server-side
concern (module selection via CLI, and eventually tool
consolidation — `freelancer` alone is 31 tools, the obvious first
candidate if that pressure ever becomes real).

NOT exposed (opinionated defaults):
- `--debug`: off. Support procedure documented in the bundle README
  instead ("run from terminal with --debug" — a support case is a
  developer moment anyway).
- `redact_paths`: **ON** — privacy-correct for someone who doesn't
  know what a path leak is. (Developers debugging keep the CLI.)
- Log/backup locations: beside the book, the existing default.
- FX guard days etc.: defaults.

### Draft manifest (abridged)

```json
{
  "manifest_version": "0.4",
  "name": "gnucash-mcp",
  "display_name": "GnuCash for Claude",
  "version": "<match pyproject>",
  "server": {
    "type": "uv",
    "entry_point": "src/gnucash_mcp/__main__.py",
    "mcp_config": {
      "command": "uv",
      "args": [
        "run", "--directory", "${__dirname}",
        "python", "-m", "gnucash_mcp",
        "--book", "${user_config.books}",
        "--redact-paths"
      ],
      "env": {
        "GNUCASH_ENABLE_FREELANCER": "${user_config.freelancer}",
        "GNUCASH_ENABLE_BUSINESS": "${user_config.business}",
        "GNUCASH_ENABLE_INVESTMENTS": "${user_config.investments}",
        "GNUCASH_ENABLE_PLANNING": "${user_config.planning}"
      }
    }
  },
  "user_config": {
    "books": {
      "type": "file", "multiple": true, "required": true,
      "title": "Your GnuCash book(s)",
      "description": "Pick one or more .gnucash files (SQLite format). Pick several to switch between them in-chat."
    },
    "freelancer": {
      "type": "boolean", "default": false,
      "title": "Client invoicing",
      "description": "Send invoices to clients and record their payments"
    },
    "business": {
      "type": "boolean", "default": false,
      "title": "Full business suite",
      "description": "Everything in Client invoicing, plus vendors, bills, and employee expenses"
    },
    "investments": {
      "type": "boolean", "default": false,
      "title": "Investment tracking",
      "description": "Lots, cost basis, capital gains"
    },
    "planning": {
      "type": "boolean", "default": true,
      "title": "Budgets & scheduled transactions"
    }
  },
  "compatibility": {
    "platforms": ["darwin", "win32", "linux"],
    "runtimes": { "python": ">=3.10" }
  }
}
```

(`${user_config.books}` with `multiple` expands to separate args —
the manifest line yields `--book A.gnucash B.gnucash`; see
prerequisite 1 for the argparse shape.)

## Server prerequisites (code changes, small)

1. **`--book` repeatable/multi-value CLI argument** as a peer of
   `GNUCASH_BOOK_PATH` (argparse `nargs="+"` so the expanded
   `--book A B` form parses). Env var remains for the CLI crowd;
   args win when both present. Kills the pathsep UX for bundle
   users entirely.
2. **Env-var module toggles** (`GNUCASH_ENABLE_BUSINESS`, etc.)
   composed into the module set at startup, defined as the
   *only* module interface the bundle uses. `--modules` still wins
   when passed explicitly.
3. **Friendly startup errors.** The #1 predictable user mistake:
   picking an XML-format GnuCash book (the format older books save
   in). The failure message must say: "This book is in GnuCash's
   XML format. Open it in GnuCash and use File → Save As →
   SQLite, then pick the new file." Audit the whole startup
   fail-fast path for messages a non-developer can act on.

## Packaging & validation

- `mcpb init` / `mcpb pack` (official CLI) produce the bundle;
  bundle contents: `manifest.json`, `src/`, `pyproject.toml`,
  `uv.lock`, icon, README. No `lib/`.
- First launch needs network (uv fetches Python + wheels once,
  then cached). Document this; corporate-lockdown machines are out
  of scope for v1.
- Validation ladder:
  1. Dev machine (macOS) — install the .mcpb into Claude Desktop,
     full bookkeeper-style smoke against a sample book copy.
  2. **Clean-machine test on Windows** — the uv race bug (#38266)
     makes this mandatory, not optional.
  3. **A genuine non-developer install** (Jesse) with zero
     coaching: the test is whether the file dialog + three
     checkboxes are actually self-explanatory.

## Open questions

- Experimental status of `type: "uv"` — re-check the spec repo and
  the Windows race issue at implementation time.
- Whether `switch_book`'s CONTEXT RESET banner needs softer
  wording for non-developer readers.
- Distribution channel: GitHub Releases artifact vs. the Claude
  Desktop extension directory (submission requirements TBD —
  check current directory policy at implementation time).

## Sources

- [MCPB spec repo (modelcontextprotocol/mcpb)](https://github.com/modelcontextprotocol/mcpb)
- [MANIFEST.md v0.4](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md)
- [hello-world-uv example manifest](https://github.com/modelcontextprotocol/mcpb/blob/main/examples/hello-world-uv/manifest.json)
- [Runtime policy issue — Node bundled, Python not (#89)](https://github.com/modelcontextprotocol/mcpb/issues/89)
- [Windows uv venv race (anthropics/claude-code#38266)](https://github.com/anthropics/claude-code/issues/38266)
- [Anthropic engineering: Desktop Extensions](https://www.anthropic.com/engineering/desktop-extensions)
- [Claude docs: Build a desktop extension with MCPB](https://claude.com/docs/connectors/building/mcpb)
