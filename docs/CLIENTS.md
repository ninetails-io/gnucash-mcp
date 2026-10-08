# Installing from source and connecting AI clients

The quickest install is the one-click Claude Desktop bundle described
in the [README](../README.md#install-in-one-click-claude-desktop).
This page covers everything else: installing from a git clone, which
every other AI client needs, as do database books and anyone working
on the server itself.

## 1. Download and install

```bash
git clone https://github.com/ninetails-io/gnucash-mcp.git
uv tool install -e ./gnucash-mcp
```

The second command gives you a `gnucash-mcp` command (in
`~/.local/bin`) with its dependencies in a private environment —
your other Python projects never see them. The `-e` makes it an
*updatable* install: the command runs whatever code is in your
clone, so updating is `git pull` plus a server restart. The one
exception: if an update changes *dependencies*, run
`uv tool install -e ./gnucash-mcp --reinstall` once.

> If you don't have `uv`, install it with one line:
> `curl -LsSf https://astral.sh/uv/install.sh | sh`

## 2. Build a sample book

A clone has no sample books, only the builders that make them, so
build one first. This builds Alex, offline, in about a minute and a
half, then copies the book out of the repo: the server writes an
audit log and backups beside a book, and those don't belong in your
clone.

```bash
cd gnucash-mcp
uv run python scripts/synthetic_book/rebuild_all.py --skip-refresh --only alex --no-promote
mkdir -p ~/gnucash-mcp-scratch
cp samples/alex.generated.gnucash ~/gnucash-mcp-scratch/alex.gnucash
```

Drop `--only alex` to build all three (about four minutes); they
land as `samples/alex.generated.gnucash`,
`samples/lin-wei.generated.gnucash`, and
`samples/sabine-brenner.generated.gnucash`.
[samples/README.md](../samples/README.md) describes each book and
every build option.

## 3. Connect your AI client

### Claude Desktop

Find your Claude Desktop config:

- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add this — replace `yourname` in both paths:

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "/Users/yourname/.local/bin/gnucash-mcp",
      "args": ["--modules=all"],
      "env": {
        "GNUCASH_BOOK_PATH": "/Users/yourname/gnucash-mcp-scratch/alex.gnucash"
      }
    }
  }
}
```

Use the **full path** to the command: GUI apps launch without
your shell's PATH, so a bare `gnucash-mcp` may not resolve even
though it works in your terminal. (`uv tool dir --bin` prints
the right directory if yours differs.) `--modules=all` loads
every tool (86 of them) so you can poke at anything. Once you
know what you actually use, narrow it — see
[choosing a module set](../README.md#choosing-a-module-set) in the README.

Quit Claude Desktop completely (not just close the window —
quit) and reopen it. Look for the hammer 🔨 icon next to the
text input. That means the server's connected.

### Other AI clients

This is an [MCP](https://modelcontextprotocol.io/) server, so
it works with any client that speaks MCP. Everywhere below,
`gnucash-mcp` means the full path from the install step
(`/Users/yourname/.local/bin/gnucash-mcp`; `uv tool dir --bin`
prints yours).

- **ChatGPT desktop / Codex (the GUI form)** — likely your path
  if this is your first time clicking "Codex." The whole setup
  is fill-in-the-blanks; no config file is involved, so there's
  nothing to break. One habit to unlearn: type every value
  bare, **no quotes** — these fields aren't a terminal, so
  quotes become literal characters in the value and the launch
  silently fails.
  1. The app opens in ChatGPT mode — click the **∨** next to
     "ChatGPT" (top left) and choose **Codex** ("Build, debug,
     and ship").
  2. Open **Settings** (the ChatGPT menu → Settings…, ⌘, on
     Mac), and under **Integrations** pick **Plugins** — not
     "Connections" under Coding, which is a different thing.
  3. Top right: **Add ∨** → **Add MCP server**. The "Connect to
     a custom MCP" form appears. **Name** it `gnucash`; leave
     **Type** on **STDIO** (the default).
  4. **Command to launch**: the full `gnucash-mcp` path from
     the install step (`uv tool dir --bin` prints the
     directory).
     *Cloned the repo but skipped the install step?* Then
     there is no `gnucash-mcp` command — launch `uv` itself:
     command = the full path to `uv` (`which uv` prints it),
     and the first four argument rows become `run`,
     `--directory`, `/path/to/your/clone`, `gnucash-mcp` —
     ahead of the `--modules=all` row below. Run `uv sync`
     once in the clone first, so the first launch isn't a
     cold dependency install racing the app's startup
     timeout.
  5. **Arguments**: `--modules=all` — one argument per row
     ("+ Add argument" for each; don't space-join several into
     one row).
  6. **Environment variables**: key `GNUCASH_BOOK_PATH`, value
     = your book's full path. Several books? Join them with `:`
     (Mac/Linux) or `;` (Windows) and switch between them
     in-chat.
  7. **Environment variable passthrough** and **Working
     directory**: leave empty. **Save** — your server appears
     under the **MCPs** tab.

  Then ask Codex "how am I doing this month?"
- **Claude Code**: `claude mcp add-json gnucash '{"command":"/Users/yourname/.local/bin/gnucash-mcp","args":["--modules=all"],"env":{"GNUCASH_BOOK_PATH":"/path/to/your/book.gnucash"}}'`
  Add `--scope user` for all projects, `--scope project` for
  this one only.
- **Codex CLI**: one command, no config file to hand-edit:
  `codex mcp add gnucash --env GNUCASH_BOOK_PATH="/path/to/your/book.gnucash" -- /Users/yourname/.local/bin/gnucash-mcp --modules=all`
  (Codex stores it in `~/.codex/config.toml`; the same config
  serves the Codex VS Code extension. `codex mcp list` confirms
  registration.)
- **Gemini CLI**: `gemini mcp add -e GNUCASH_BOOK_PATH="/path/to/your/book.gnucash" gnucash /Users/yourname/.local/bin/gnucash-mcp --modules=all`
  This writes a project `.gemini/settings.json` with the server
  registered; run `/mcp list` inside Gemini to confirm it shows
  `gnucash - Ready`. (Verified on Linux — if GnuCash never offered
  a SQLite3 export, see the `libdbd-sqlite3` note in
  [the README](../README.md#one-time-conversion-gnucash-file-format). The Gemini
  walkthrough and the Linux driver fix both come from
  [@hpuri](https://github.com/hpuri)'s testing in
  [#89](https://github.com/ninetails-io/gnucash-mcp/issues/89) —
  thanks.)
- **Google Antigravity (IDE or CLI)**: add the server to
  `~/.gemini/config/mcp_config.json` (global) or your
  workspace's `.agents/mcp_config.json`:
  ```json
  {
    "mcpServers": {
      "gnucash": {
        "command": "/home/yourname/.local/bin/gnucash-mcp",
        "args": ["--modules=all"],
        "env": { "GNUCASH_BOOK_PATH": "/path/to/your/book.gnucash" }
      }
    }
  }
  ```
  Use the absolute command path. On Linux the same
  `libdbd-sqlite3` note as the Gemini walkthrough applies if
  GnuCash won't offer a SQLite3 save format.
- **Anything else**: set `GNUCASH_BOOK_PATH` and run
  `gnucash-mcp`. No install at all? `uv run --directory
  /path/to/gnucash-mcp gnucash-mcp` and
  `python -m gnucash_mcp` (with the repo on the path) both
  still work. Any client that can spawn a command and speak
  MCP over stdio will do.

### Using your own book

Update `GNUCASH_BOOK_PATH` in your client's config to point at your
own SQLite-format book (see
[the one-time conversion](../README.md#one-time-conversion-gnucash-file-format)
if it's still XML), then restart the client.

> **Use absolute paths**, not `~` or relative paths. On
> Mac/Linux: `/Users/yourname/Documents/mybook.gnucash`. On
> Windows: `C:\\Users\\yourname\\Documents\\mybook.gnucash`
> (note the doubled backslashes — that's a JSON requirement).

## 4. Try it

Ask your assistant "Summarize the book." The first response is the
dashboard shown in the [README](../README.md#what-does-it-look-like),
and everything after that is conversational. More to try:
[What you can ask it to do](../README.md#what-you-can-ask-it-to-do).
