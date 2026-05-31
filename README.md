# gnucash-mcp

**Free, open-source accounting software that works with the LLM.**

Talk to your GnuCash books through Claude (or any AI assistant
that supports MCP). Ask "how am I doing this month," dictate
your transactions out loud, hand over the books for the AI to
keep up while you focus on running your life or your business.

Your data stays on your machine. Your audit log stays on your
machine. Nothing is uploaded anywhere — the AI reads and writes
your local GnuCash file, and that's it.

Two real, populated sample books ship in this repo so you can
try it before you commit anything. They're realistic — full
years of activity, mixed currencies, customers, invoices,
budgets, the works. Walk through one in five minutes; if it
clicks, point the server at your own book and you're done.

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
  Checking Account: 174 splits unreconciled since 2025-12-30 (4 months behind) ⚠
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

The repo ships two sample books — fully-populated synthetic
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

Both books are fictional. See [samples/README.md](samples/README.md)
for the full breakdown of what's in each.

---

## Quick Start (5 minutes)

### 1. Install

```bash
git clone https://github.com/ninetails-io/gnucash-mcp.git
uv tool install --editable ./gnucash-mcp
```

That gives you a `gnucash-mcp` command on your PATH. `--editable`
keeps it tracking the source, so a `git pull` shows up in the
running server the next time it restarts. Skip `--editable` if
you don't plan to update.

> If you don't have `uv`, install it with one line:
> `curl -LsSf https://astral.sh/uv/install.sh | sh`

> For developers working against multiple worktrees, plain
> `uv sync` inside the project directory still works — see
> [For developers](#for-developers) below.

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

Add this (replace the `GNUCASH_BOOK_PATH` value with your actual
path):

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "gnucash-mcp",
      "args": ["--modules=all"],
      "env": {
        "GNUCASH_BOOK_PATH": "/Users/yourname/gnucash-mcp-scratch/alex.gnucash"
      }
    }
  }
}
```

`--modules=all` loads every tool (106 of them) so you can poke at
anything. Once you know what you actually use, narrow it — see
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
it works with any client that speaks MCP. Notes for non–Claude
Desktop clients:

- **Claude Code**: `claude mcp add-json gnucash '{"command":"gnucash-mcp","args":["--modules=all"],"env":{"GNUCASH_BOOK_PATH":"/path/to/your/book.gnucash"}}'`
  Add `--scope user` for all projects, `--scope project` for
  this one only.
- **Anything else**: set `GNUCASH_BOOK_PATH` and run
  `gnucash-mcp` directly. Any client that can spawn a command
  and speak MCP over stdio will work.

---

## Choosing a module set

`--modules=all` is the easy default — every tool, 106 of them.
For day-to-day use you'll probably want less. Pick the role that
matches how you'll talk to the server. Each role is a *group*
that expands to the underlying tool modules; you can also pick
the leaves individually for a finer cut.

| Role | What it gives you | Tools |
|---|---|---|
| `core` | Ledger primitives — accounts, transactions, balances, slots, audit log, backups, balance sheet, **reconciliation**. **Always loaded.** | 29 |
| `bookkeeper` | Run reports, manage budgets, schedule recurring transactions. The personal-finance management cluster. (Reconciliation moved into core — any configuration that handles money needs it.) | 17 |
| `investor` | Cost-basis tracking + price/commodity management. Tax-lot accounting needs prices to compute gains, so the bundle is the useful unit. | 12 |
| `freelancer` | Customer invoicing + sales tax, plus billterms (payment terms), jobs (per-project P&L rollups), and credit notes (customer refunds). The full solo-consultant toolkit. | 31 |
| `business` | Full small-business package — group alias that expands to `freelancer` (invoicing) plus `business_complete` (vendors, employees, bills, vouchers, vendor reports). | 48 |

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
run `gnucash-mcp --help` for the full menu.

---

## What you can ask it to do

A non-exhaustive tour. Phrase any of these naturally — the
assistant translates.

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
valid. See [docs/RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md)
for the rollback procedure.

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

## What's in v1.2.1

This release is the long-tail completion of the v1.2 business-
module promise — what v1.2 should have been at first ship,
plus a generous correctness sweep on every non-USD-default
path the test books surfaced.

**Major:**

- **Multi-currency, end-to-end.** Foreign-currency invoices
  post and pay against the right exchange rates from your
  price table; rate drift between post-date and pay-date is
  recognized as realized FX gain/loss in a dedicated income
  account (or one you specify).
- **A complete first-call dashboard.** `get_book_summary` now
  shows net-worth trajectory, runway, monthly net income,
  budget pacing, reconciliation backlog with split counts,
  upcoming bills, and warnings — turning the LLM's first call
  from "what is the state of the books" into "what do I need
  to do next."
- **Customer / vendor / employee CRUD complete.** Create,
  list, get, update (new in 1.2.1), and delete. No more
  needing to open GnuCash itself just to fix a typo on an
  address.
- **Posting workflow lifecycle complete.** `post_invoice` and
  `pay_invoice` joined by `unpost_invoice` (new) so a posted
  invoice can be reversed cleanly without SQL surgery.
  `delete_transaction` refuses to break the lifecycle by
  removing posting records directly.
- **Automatic backups.** First write of each session snapshots
  the book; staged retention (7 session / 4 weekly / 6
  monthly) keeps you covered without filling your disk.

**Plus a thousand smaller fixes** from intensive testing on
two real-shape books — multi-currency reporting, debt-payoff
amortization for mortgages, voided-payment handling, employee-
expense-voucher hooks, and many more. See
[specs/NEXT_STEPS_1_3.md](specs/NEXT_STEPS_1_3.md) for the 1.3
roadmap (taxtables, jobs, credit notes, employee expense
vouchers).

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
uv run pytest                       # 1,355 tests as of v1.3.0
uv run ruff check src/ tests/
uv run black --check src/ tests/
```

For dev work the `uv run --directory PATH ...` form is the
escape hatch — it lets you point Claude Desktop at a specific
worktree without installing. The `uv tool install --editable`
path from the Quick Start is generally cleaner for everyday
use because the `gnucash-mcp` binary tracks your source.

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
