# gnucash-mcp

**Free, open-source accounting software that works with the LLM.**

Talk to your GnuCash books through Claude (or any AI assistant
that supports MCP). Ask "how am I doing this month," dictate
your transactions out loud, hand over the books for the AI to
keep up while you focus on running your life or your business.

Your data stays on your machine. Your audit log stays on your
machine. Nothing is uploaded anywhere — the AI reads and writes
your local GnuCash file, and that's it.

**Install in one click:** on Claude Desktop, download the
`.mcpb` bundle from the
[latest release](https://github.com/ninetails-io/gnucash-mcp/releases/latest),
double-click it, and you're running — no terminal, no config
files. Every other MCP client — ChatGPT/Codex, Gemini,
Antigravity, and the rest — connects with
[a few lines of setup](#other-ai-clients). Either way, the AI
subscription you already pay for becomes a bookkeeper that never
sends a bill.

Three real, populated sample books ship in this repo so you
can try it before you commit anything. They're realistic — full
years of activity, mixed currencies, customers, invoices,
budgets, the works. Walk through one in five minutes; if it
clicks, point the server at your own book and you're done.

The samples are living books: the committed copies are frozen at
their last update, and the closed-loop updater
(`scripts/synthetic_book/continue_book.py <persona>`) brings any
of them current through today — statement payments from real
balances, invoices settled, accounts reconciled. The bundle ships
them current as of its build day. An un-updated copy just looks
like a book after a vacation — stale prices, pending scheduled
transactions — which is realistic too.

---

## What does it look like?

This is what your AI assistant sees when it opens one of the
sample books — a complete financial dashboard in a single call:

```
Book: samples/alex-chen-morales.gnucash
Currency: USD
Data range: 2025-01-01 to 2026-05-31
Last entry: 2026-05-31 (future-dated, 31 days ahead)
Warnings:
  ⚠ Past due invoice: Berlin Digital GmbH 58 days past 30-day default, EUR 4,200 (no term set)
  ⚠ Stale price: GBP last updated 150 days ago
Accounts: 108 total
Assets: 12 accounts, USD 602680.49
  Condo: USD 473250.00
  VTSAX: 230.7620 VTSAX @ 170.99 (USD 39457.99)
  Vehicle: USD 27845.00
  401k: USD 13404.62
  Checking Account: USD 12393.11
  ...
Liabilities: 4 accounts, USD 418457.79
  Credit cards (2): USD 38044.26
  Loans (2): USD 380413.53
  Top 3: Mortgage USD 372199.55, Chase Sapphire USD 22383.23, Business Amex USD 15661.03
Receivables: 3 accounts, USD 10246.46
  Accounts Receivable EUR: USD 4908.96
  Accounts Receivable: USD 3500.00
  Accounts Receivable CAD: USD 1837.50
Reconciliation:
  Checking Account: 174 splits unreconciled (4 months behind, oldest: 2025-12-30) ⚠
  7 accounts never reconciled ⚠
Net worth trajectory:
  12mo ago: USD 187,925
   6mo ago: USD 180,614
   3mo ago: USD 191,350
   1mo ago: USD 185,444
       now: USD 184,223
Monthly net (last 6 months):
  Apr 2026 (MTD): -9,056
  Mar 2026: +3,092
  Feb 2026: +5,202
  Jan 2026: +1,086
  Dec 2025: +4,853
  Nov 2025: -1,494
Runway: 121 days (USD 84,579 liquid / USD 694/day burn)
Budget (2026 Annual Budget): 41% used / 33% elapsed (+8% over pace)
Transactions: 2473
Scheduled: 13 recurring, none due in next 7 days
Business: 4 customers, 2 vendors, 1 employee
```

That's not a screenshot — that's the AI's actual orientation
view. Net worth trajectory, runway, budget pacing, who owes you
money, what's overdue, what hasn't been reconciled. One call,
and your assistant has the full picture before you've even
finished saying hello.

---

## Who is this for?

- **Personal finance people** who keep their books in GnuCash
  and want to dictate transactions, ask their assistant where
  the money's going, get reconciliation help, plan budgets.
- **Small business owners** who run their books in GnuCash and
  want to issue invoices, track receivables, see vendor
  spending, manage cash flow without leaving the conversation.
- **People who care that their data stays local.** No cloud
  sync. No SaaS. Your `.gnucash` file is the system of record;
  this just gives your AI a way to read and write it the way
  GnuCash itself does.

You don't need to be a developer. You need:

- A computer (Mac, Windows, or Linux)
- GnuCash itself, or willingness to install it (free at
  [gnucash.org](https://www.gnucash.org/))
- An AI assistant that supports MCP (Claude Desktop is the
  most common; Claude Code, Continue.dev, and others work too)
- 10 minutes to get the sample books running, then another 10
  to point at your own

---

## Try it without risking anything

The repo ships three sample books — fully-populated synthetic
ledgers you can talk to without touching your real data. Pick
one, point the server at it, and start asking questions.

### `samples/alex-chen-morales.gnucash` — Personal + freelance

A Seattle-based independent software contractor with a US LLC.
USD-default. ~141 accounts, ~2,475 transactions across 2025–
2026. Has a mortgage, a brokerage with VTSAX/VBTLX/AAPL/MSFT/ETH
holdings, a 401(k), four customers spanning USD/EUR/GBP/CAD with
foreign-currency invoices, scheduled bills, a budget — pretty
much everything the server can do, all in one book.

### `samples/lin-wei.gnucash` — Cross-border small business

A Shenzhen-based small-business owner running a cross-border
e-commerce operation. CNY-default. ~105 accounts, ~1,960
transactions. Chinese-named customers paying in CNY, USD/EUR
customers paying in foreign currency with realized FX gain/loss
on rate moves, domestic Chinese investments (茅台, 宁德时代,
ETFs), an LPR-based mortgage, mixed payment rails (checking +
Alipay + WeChat Pay).

### `samples/sabine-brenner.gnucash` — German freelancer, SKR03 chart

A Munich-based freelance consultant. EUR-default, on a German
SKR03 chart of accounts — every account name in German. ~110
accounts, ~1,500 transactions. This is the i18n oracle: if a
feature secretly assumes English account names or USD, Sabine's
book is where it breaks.

All three books are fictional. See
[samples/README.md](samples/README.md) for the full breakdown of
what's in each.

---

## Quick Start

### The one-click way (Claude Desktop)

Download the **`.mcpb` bundle** from the
[latest release](https://github.com/ninetails-io/gnucash-mcp/releases/latest)
and double-click it. Claude Desktop installs the server — no
terminal, no config file, no Python. The installer asks three
things:

- **Your GnuCash book(s)** — a file picker. Books must be in
  SQLite format; if yours is the older XML format, do the
  [one-time conversion](#one-time-conversion-gnucash-file-format)
  first. Pick several books to switch between them in-chat.
- **Demo books** — one checkbox serves the three sample books
  described above, so you can explore on fictional money before
  (or instead of) connecting your own.
- **"Do you invoice clients?"** — yes adds the business suite
  (customer invoices, vendor bills, employee expenses).
  Everything else — budgets, scheduled transactions, investment
  tracking — is always on.

That's the entire install. Skip ahead to
[step 4](#4-try-it) to take it for a spin.

### The manual way (any MCP client, or development)

The path below gives you an updatable git-clone install — for
Claude Desktop without the bundle, for
[other AI clients](#other-ai-clients), or for hacking on the
server itself.

### 1. Download and install

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

### 2. Make a working copy of a sample book

The server writes audit logs and auto-backups alongside the
book file. You don't want either of those committed back to the
repo, so copy the book somewhere outside the repo first:

```bash
mkdir -p ~/gnucash-mcp-scratch
cp gnucash-mcp/samples/alex-chen-morales.gnucash ~/gnucash-mcp-scratch/alex.gnucash
```

### 3. Tell Claude Desktop about the server

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
[choosing a module set](#choosing-a-module-set) below.

Quit Claude Desktop completely (not just close the window —
quit) and reopen it. Look for the hammer 🔨 icon next to the
text input. That means the server's connected.

### 4. Try it

Ask Claude:

- "Summarize the book."
- "What's my net worth been doing?"
- "Show me anyone who owes me money."
- "What did I spend on dining last month?"
- "Set a $500 monthly grocery budget."

The first response usually starts with the dashboard from
above. Everything after that is conversational.

When you're ready to point at your own book, replace the
`GNUCASH_BOOK_PATH` value with the path to your real `.gnucash`
file (more on that next), restart Claude Desktop, and ask away.

---

## Connecting to your own book

### One-time conversion: GnuCash file format

The server only reads the **SQLite** form of GnuCash files, not
the older XML form. To convert:

1. Open your book in GnuCash itself
2. **File → Save As**
3. Change "Data Format" to **SQLite3**
4. Save with a new filename (e.g. `mybook-sqlite.gnucash`)
5. **Keep the XML original as a backup.**

**On Linux (Debian/Ubuntu),** SQLite3 may be missing from the
"Data Format" drop-down entirely — GnuCash needs a backend driver
that isn't installed by default. Close GnuCash, install it, then
reopen and the option appears:

```bash
sudo apt update && sudo apt install libdbd-sqlite3
```

You only do this once. From then on, GnuCash and the MCP server
both work against the same SQLite file.

### Set the path

Update `GNUCASH_BOOK_PATH` in your Claude Desktop config to
point at your own SQLite-format book. Restart Claude Desktop.

> **Use absolute paths**, not `~` or relative paths. On
> Mac/Linux: `/Users/yourname/Documents/mybook.gnucash`. On
> Windows: `C:\\Users\\yourname\\Documents\\mybook.gnucash`
> (note the doubled backslashes — that's a JSON requirement).

### Other AI clients

This is an [MCP](https://modelcontextprotocol.io/) server, so
it works with any client that speaks MCP. Everywhere below,
`gnucash-mcp` means the full path from the install step
(`/Users/yourname/.local/bin/gnucash-mcp`; `uv tool dir --bin`
prints yours).

- **ChatGPT desktop / Codex (the GUI form)** — likely your path
  if this is your first time clicking "Codex." The whole setup
  is fill-in-the-blanks; no config file is involved, so there's
  nothing to break.
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
     the install step.
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
  a SQLite3 export, see the `libdbd-sqlite3` note above. The Gemini
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

---

## Choosing a module set

`--modules=all` is the easy default — every tool, 86 of them.
For day-to-day use you'll probably want less. Pick the role that
matches how you'll talk to the server. Each role is a *group*
that expands to the underlying tool modules; you can also pick
the leaves individually for a finer cut.

| Role | What it gives you | Tools |
|---|---|---|
| `core` | Ledger primitives — accounts, transactions, balances, slots, audit log, backups, balance sheet, **reconciliation**. **Always loaded.** | 29 |
| `bookkeeper` | Run reports, manage budgets, schedule recurring transactions. The personal-finance management cluster. (Reconciliation moved into core — any configuration that handles money needs it.) | 17 |
| `investor` | Cost-basis tracking + price/commodity management. Tax-lot accounting needs prices to compute gains, so the bundle is the useful unit. | 12 |
| `freelancer` | Party + document management (polymorphic: customers by default; vendors/employees unlock with `business_complete`), sales tax, billterms, jobs, credit notes. The full solo-consultant toolkit. | 26 |
| `business` | Full small-business package — group alias: `freelancer`'s tools with the vendor/employee sides unlocked, plus vendor reports. | 27 |

Pick one or more, comma-separated:

```json
"args": ["--modules=bookkeeper"]            // personal finance
"args": ["--modules=investor"]              // self-directed investor
"args": ["--modules=freelancer"]            // solo contractor
"args": ["--modules=business"]              // small business (= freelancer + business_complete)
"args": ["--modules=bookkeeper,investor,freelancer"]  // most things
```

`core` is force-added regardless; the explicit listing in the
examples above is for clarity. The leaf modules behind each
group (`reconciliation`, `reporting`, `budgets`, `scheduling`,
`tax_lots`, `portfolio`, etc.) are individually selectable too —
run `uv run gnucash-mcp --help` from the repo for the full menu.

---

## What you can ask it to do

A non-exhaustive tour. Phrase any of these naturally — the
assistant translates.

### Entering a whole statement

> "Here's my August checking statement." *(attach the PDF)*
>
> Rehearsed 31 lines against your book: 24 new, 6 already
> entered (claimed), 1 needs a look — here's the comparison.
> Confirm and I'll land the month: entered, categorized, and
> reconciled to the closing balance in one step.

One statement, two calls, a tied book. The dry-run classifies
every line with evidence before anything is written, and the
commit refuses wholesale rather than land a month that doesn't
tie.

### Recording activity

> "I spent \$47.50 at Safeway today on groceries, paid with my
> Visa."
>
> Recorded: \$47.50 from Credit Card:Visa to
> Expenses:Groceries on Apr 30, 2026. Description: "Safeway".

### Setting up recurring bills

> "Set up my rent as a recurring bill. \$1,850 on the 1st of
> every month from checking."
>
> Created scheduled transaction "Monthly Rent": \$1,850
> monthly, from Assets:Checking to Expenses:Rent. Next due:
> May 1, 2026.

### Investment tracking

> "I bought \$1,000 of VTSAX at \$170.99/share in my 401(k)."
>
> Recorded VTSAX purchase: 5.85 shares at \$170.99/share. Cost
> basis: \$1,000.00. Lot created for capital-gains tracking
> when you eventually sell.

### Invoicing customers

> "Create a customer called Acme Corp and invoice them \$1,500
> for consulting."
>
> Created customer Acme Corp (id 000005) and invoice 000019:
> 1× Consulting at \$1,500.00 = \$1,500.00. Open. Tell me when
> you're ready to post it.

### Foreign-currency invoicing

> "Invoice Berlin Digital €4,200 for Q1 retainer, due in 30 days."
>
> Created EUR invoice 000020 for Berlin Digital GmbH:
> 1× Q1 Retainer at EUR 4,200 = EUR 4,200. Net 30. When they
> pay, I'll book the realized exchange-rate gain/loss to your
> FX account automatically.

### Asking questions

> "How much did I spend with each vendor this year?"
>
> Office Depot: \$2,340 (4 bills, \$0 outstanding)
> CloudHost Inc: \$1,200 (2 bills, \$600 outstanding)
> Legal Associates: \$3,500 (1 bill, \$3,500 outstanding)
> Total billed \$7,040 / paid \$2,940 / outstanding \$4,100.

### Reconciliation

> "Help me reconcile checking against the April statement."
>
> [Walks you through it: pulls unreconciled splits, asks you
> to confirm cleared transactions, computes the running
> balance, marks the matched ones reconciled, leaves the
> mismatches for you to investigate.]

---

## Privacy and safety

**Your data does not leave your machine.** This server is a
local process that reads and writes a local file. The AI
assistant you're talking to (Claude Desktop, etc.) sees the
results of your tool calls — the same content you'd see on
screen — but the file itself stays where it always was.

**Every write is logged.** A human-readable audit trail lives
alongside your book file at `<your-book>.gnucash.mcp/audit/`,
one log file per day. You can read it at any time to see
exactly what changed and when. Sample entry:

```
2026-04-30 14:32  POST INVOICE  id:000019
    total: 1500.00  date: 2026-04-30
    account: Assets:Accounts Receivable  txn:a1b2c3d4
```

**Automatic backups.** Before the very first write of each
session, the server snapshots your book to
`<your-book>.gnucash.mcp/backups/` — so if something goes
wrong, you can roll back to a known-good state without
relying on Time Machine or your own habit. Backups are
verified with `PRAGMA integrity_check` before being declared
valid, and skipped when the book hasn't changed since the
last snapshot. See [docs/RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md)
for the rollback procedure.

> **Reading timestamps:** backup *filenames* carry UTC
> timestamps (filesystem-safe and unambiguous across travel
> and DST); audit and debug logs use *local-dated* daily
> files, matching how you'd search for "what happened
> Tuesday." Near midnight these can differ by a day — keep
> that in mind when matching a backup to a day's log.

**Reconciled splits are protected.** The server refuses to
delete or modify reconciled splits without an explicit
override, so a careless prompt can't quietly invalidate your
last bank reconciliation.

**Voiding ≠ deleting.** When you tell the AI to "void this
transaction," it uses GnuCash's proper accounting void —
preserving the transaction for the audit trail with values
zeroed. Deletion is the destructive option; the AI will tell
you which one it's doing.

> **Disclaimer:** This software is provided "as is" under the
> [MIT License](LICENSE), without warranty of any kind. The
> authors are not liable for any data loss, corruption, or
> financial discrepancy arising from its use. You are solely
> responsible for maintaining your own backups and verifying
> the accuracy of your books.

---

## Limiting what the AI can see

Each tool's description lives in the AI's system prompt, which
costs context on every message. Narrowing the toolset to what
you actually use makes every conversation cheaper. See
[choosing a module set](#choosing-a-module-set) above for the
five role-based options (`core`, `bookkeeper`, `investor`,
`freelancer`, `business`).

You can also set `GNUCASH_MCP_MODULES=core,bookkeeper` as an
environment variable instead of `--modules=...` in the JSON
args.

---

## What's new in v1.4.4

The statement is the call — the bulk-operations line closes with
its capstone, and rehearsal spreads to every consequential write:

- **`enter_statement`** — a complete bank statement (opening
  balance, closing balance, every line) enters, claims its
  matches against transactions already in the book, and
  reconciles in ONE atomic call. Dry-run first by default: every
  line classified with side-by-side evidence, and a projected
  balance tie that guarantees a rehearsal that ties is a commit
  that will tie. No half-landed months, ever.
- **Rehearsal everywhere** — `pay_document` gains `dry_run`
  (proposed splits, FX and discount treatment, projected balance,
  zero writes), and batch entry's dry-run shows self-contained
  duplicate comparisons with a `review_required` status that
  never masquerades as clearance.
- **One-click install** — the MCPB bundle: download, double-click,
  and Claude Desktop runs the server. Built by the project's first
  CI on every PR.
- **A surface that tells the truth about itself** — MCP
  ToolAnnotations on every tool (read-only says so, destructive
  says so), strict CLI arguments, a defined status vocabulary,
  and a debt plan that names every debt it had to leave out.
- **The un-blooming, completed in one release** — the tool
  surface peaked at 111 and ships at 86: the business surface
  consolidated (48 tools → 27, one polymorphic family per verb),
  and the batch tools are now THE entry/update tools (the
  singular create/update removed at full capability parity).

**Tests:** 2,100+ passing, parallel by default (full suite < 40s).

## What's new in v1.4.2

One call wide, every surface honest — every entry traces to a
named moment of live friction:

- **The bulk grammar is complete** — `update_transactions`
  (per-row TSV edits), broadcast updates (one change, many GUIDs),
  `create_prices` (batch quotes + a stale-price work list), and a
  `cur` column so foreign-denominated transactions batch-enter
  like everything else.
- **Reconciliation kept honest** — `reconcile_all` honors its
  statement-date bound; a new `get_reconciliation_status` tool
  drills down behind the dashboard's counts; statement-less
  accounts opt out of nagging with the `no_reconcile` slot; paid-off
  dormant cards stop warning forever.
- **The dashboard hands each session its vocabulary** — your top
  accounts by recent posting frequency, in short-GUID form, so the
  AI reaches for compact refs from the first call.
- **First outside code contribution** — @bhbrunt's price-lookup
  memoization and split-graph preload took a 33k-split book's
  summary from never-completing to under 10 seconds (and made
  small books ~45% faster too).
- **Audit trail hardened** — user text is escaped before it
  reaches the audit log (no forged entries, no smuggled
  instructions), price dry-runs agree with live execution, and
  moving the date of a reconciled transaction now requires
  `force=true` (behavior change).

**Tests:** 1,954 passing.

## What's new in v1.4.1

Batch entry grows up, driven by the bookkeeper's daily workflow:

- **The TSV header declares the layout** — opt-in `memo` columns
  (per-split memos), a `notes` column (per-transaction notes), and
  `qty` columns (investment shares / foreign-currency splits).
  Legacy submissions parse unchanged; typo'd column names reject
  by name; a row may simply end once its last split's amount and
  account are present.
- **Auto-fill from history** — a row with no split cells at all
  reproduces your most recent transaction with that description,
  marked with its source. Twelve recurring bills = twelve
  ref-date-description rows; `dry_run` the batch to preview every
  match first.
- **Batch delete** — `delete_transaction` takes a list of GUIDs:
  one call, one save, all-or-nothing.
- **Every annotation field reachable** — notes + action on
  invoice/bill/voucher/credit-note line items, a payment memo on
  `pay_document`, account notes (shared with GnuCash desktop's
  editor), and scheduled transactions that actually keep their
  description.
- **Find accounts without paging** — `query` on `list_accounts`
  matches path and description, so "4930" finds the SKR03 account.
- Plus the v1.4 adversarial-review hardening (transactional
  `switch_book`, per-book backup scoping, i18n fixes) and
  monthly-close valuation for flow reports.

**Tests:** 1,856 passing.

## What's in v1.4.0

The release where batch transaction entry entered the scene.
v1.3 finished the business
module; v1.4 makes the server work correctly on non-English books,
adds bulk and multi-book workflows, and lands a second
multi-currency correctness pass.

**Internationalization:**

- Account resolution keys off `GNCAccountType`, never a localized
  account *name* — so a `de_DE`, `es_MX`, or `zh_CN` book resolves
  Income, Imbalance, and FX accounts correctly. Designated
  accounts (FX gain/loss, discounts) self-heal via a KVP slot that
  is locale- and rename-proof after first use.
- Suspense / Imbalance accounts are excluded from runway and
  low-cash signals so a lopsided book doesn't skew the dashboard.
- Three synthetic personas ship in-repo: **Alex** (USD), **Lin
  Wei** (CNY, zh_CN chart of accounts), and **Sabine Brenner**
  (German DATEV SKR03, EUR) — the German book is what makes the
  i18n bug class visible.

**Batch and multi-book workflows:**

- `create_transactions` enters many transactions in one atomic
  call and returns a per-transaction result you can correlate back
  by a caller-supplied `ref`, plus a duplicates table keyed to it.
- `GNUCASH_BOOK_PATH` accepts an `os.pathsep`-separated list of
  books; `switch_book` flips the active book mid-session (matched
  by unique filename prefix) with a context-reset banner so
  cross-book references don't leak.

**Reporting:**

- Every list-returning tool paginates with `offset` and a
  `Showing X-Y of Z` indicator; dated tools also render the
  covered date range.
- The aggregation reports take `group_by` for sub-period columns.

**Multi-currency correctness (second pass):**

- FX gain/loss booked in the book's default currency, both-foreign
  posting splits valued at the posting-date rate, and lot cost
  basis in the default currency. Foreign debts with no FX rate are
  excluded from `debt_payoff_plan` with a warning.
- An FX entry-sanity warning fires when a cross-currency
  transaction's implied rate diverges sharply from the latest
  price on file.

**Tests:** 1,714 passing.

A condensed changelog of major releases lives in
[CHANGELOG.md](CHANGELOG.md).

---

## Troubleshooting

### No 🔨 hammer icon, or "tool not found"

- Quit Claude Desktop completely, then reopen it. (Closing the
  window isn't enough — you have to quit the application.)
- Verify the paths in your config are absolute and correct.
- Check the JSON for trailing commas — they break the config
  silently.

### "Book not found"

- Use absolute paths, not `~` or relative paths.
- Mac/Linux: `/Users/yourname/Documents/book.gnucash`
- Windows: `C:\\Users\\yourname\\Documents\\book.gnucash`
  (doubled backslashes — JSON requirement)

### "Cannot open book" / piecash errors

- Confirm your book is in **SQLite** format, not XML.
- Make sure GnuCash isn't open with the same book — file lock.
- Try opening the book in GnuCash itself to verify it isn't
  corrupted.

### "Account not found"

- Use full account paths: `Expenses:Groceries`, not just
  `Groceries`.
- Or ask the assistant to list accounts: "List my accounts."

### Multiple server processes after a client restart

Claude Desktop (and some other MCP clients) may briefly spawn
two or three copies of the server when relaunching. This is
client behavior, not a server bug, and it's mostly harmless:
the server opens your book per-request and releases the file
lock between calls, so overlapping processes contend only for
moments. If you see persistent `Lock on the file` errors after
a client restart, quit the client fully, confirm with
`pgrep -fl gnucash-mcp` that no strays remain, and relaunch.

### Something went wrong

- Open the audit log at `<your-book>.gnucash.mcp/audit/` —
  every write since the server first ran is there with
  before/after detail.
- If you need to roll back, [docs/RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md)
  walks through it.

---

## Support the project

If gnucash-mcp is useful to you, consider
[buying me a coffee](https://ko-fi.com/gomezfox). It helps
keep development going.

---

## For developers

Contributor guide and design notes live in
[CLAUDE.md](CLAUDE.md). Quick orientation:

```bash
uv sync --extra dev
uv run pytest                       # 2,100+ tests as of v1.4.4, parallel by default
uv run ruff check src/ tests/
uv run black --check src/ tests/
```

The installed `gnucash-mcp` command tracks your clone live: it
serves whatever branch the checkout is on, so switching branches
switches the served code at the next restart — handy for testing,
worth remembering when you forget you're mid-branch. To run a
DIFFERENT checkout (a second worktree) without touching the
install, `uv run --directory PATH gnucash-mcp` still runs any
directory you point it at.

The server is built on
[piecash](https://github.com/sdementen/piecash) (Python
interface to GnuCash's SQLite books) and the
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
Roughly 18,000 lines of Python source, 20,000 lines of tests,
modularized so disabled modules cost nothing at runtime.

## License

[MIT](LICENSE).

## Acknowledgments

- [GnuCash](https://www.gnucash.org/) — the free, open-source
  accounting software this server makes conversational.
- [piecash](https://github.com/sdementen/piecash) — Python
  interface to GnuCash SQLite books.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) —
  the Model Context Protocol implementation.
