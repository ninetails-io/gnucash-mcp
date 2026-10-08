# gnucash-mcp

**Free, open-source accounting software that works with the LLM.**

Talk to your GnuCash books through Claude (or any AI assistant
that supports MCP). Ask "how am I doing this month," dictate
your transactions out loud, hand over the books for the AI to
keep up while you focus on running your life or your business.

The server runs on your machine and works on your local GnuCash
file; the file and its audit log never leave it. Your AI
assistant sees what its tool calls return (balances, payees,
invoices), just as you would on screen.

**Upgrading from 1.4?** Read [the upgrade guide](docs/UPGRADING.md)
before your first write with 1.5.

**Install in one click:** on Claude Desktop, download the
`.mcpb` bundle from the
[latest release](https://github.com/ninetails-io/gnucash-mcp/releases/latest),
double-click it, and you're running — no terminal, no config
files. Every other MCP client — ChatGPT/Codex, Gemini,
Antigravity, and the rest — connects with
[a few lines of setup](docs/CLIENTS.md). Either way, the AI
subscription you already pay for becomes a bookkeeper that never
sends a bill.

Three realistic sample books let you try it before you commit
anything: full years of activity, mixed currencies, customers,
invoices, and budgets. The Claude Desktop bundle includes them;
from a clone, [one command builds them](samples/README.md). Walk
through one in five minutes; if it clicks, point the server at
your own book and you're done.

---

## What does it look like?

This is what your AI assistant sees when it opens one of the
sample books — a complete financial dashboard in a single call:

```
Book: alex-chen-morales.gnucash
Currency: USD
Data range: 2025-01-01 to 2026-10-08
Last entry: 2026-10-08 (today)
Chart of accounts: 111 total (109 active) — drill into any branch with list_accounts(root="Assets:Investments"):
  Assets (25 total): Investments (9), Current Assets (5), Fixed Assets (3), Receivables (3), Retirement (3)
  Liabilities (8 total): Credit Card (3), Loans (3)
  Equity (3 total)
  Income (11 total): Investment Income (6)
  Expenses (64 total): Business (14), Taxes (10), Utilities (6), Auto (4), Housing (4), Interest (4), Insurance (3), Pet (3)
Assets: USD 874270.54
  Condo: USD 475000.00
  VTSAX: 453.4039 VTSAX @ 185.53 (USD 84120.03)
  UWRP 403(b): USD 74778.40
  Savings Account: USD 60000.00
  Cascade Code LLC Checking: USD 41390.64
  ...
Liabilities: USD 389695.79
  Credit cards (2): USD 1319.99
  Loans & other (2): USD 385873.30
  Top 3: Mortgage USD 373846.80, Auto Loan USD 12026.50, Chase Sapphire USD 876.31
Receivables: 2 accounts, USD 27324.47 (4 invoices, 0 overdue; included in Assets total)
  Accounts Receivable: USD 22425.00
  Accounts Receivable EUR: USD 4899.47
Payables: 1 account, USD 2502.50 (1 bill, 0 overdue; included in Liabilities total)
  Accounts Payable: USD 2502.50
Jobs: 3 active
Frequently used accounts (last 180 days — any account parameter accepts the %guid or the full name; a %guid is fewer tokens and faster to write):
  %528b7d9	Assets:Current Assets:Checking Account [BANK]
  %8799a0a	Liabilities:Credit Card:Chase Sapphire [CREDIT]
  %839fcf8	Expenses:Dining
  ...
Reconciliation:
  6 accounts current
Net worth trajectory:
  12mo ago: USD 345,969
   6mo ago: USD 381,961
   3mo ago: USD 429,392
   1mo ago: USD 443,546
       now: USD 484,575
Monthly net (income - expenses, last 6 months):
  Oct 2026 (MTD): +11,955 (vs Sep 1-8: -2,365)
  Sep 2026: +25,894
  Aug 2026: +7,588
  Jul 2026: +10,307
  Jun 2026: +24,092
  May 2026: +9,017
Runway: 579 days (USD 246,844 liquid / USD 426/day cash out incl. debt paydown, 180-day avg; cards owe USD 1,320)
Budget (2026 Annual Budget): USD 27,736 spent / USD 28,683 expected by today (-3%)
Transactions: 2227
Scheduled: 20 recurring, 16 due in next 7 days (USD 15,128 out)
Business: 8 customers, 3 vendors
Budgets: 2
Commodities: AAPL, CAD, ETH, EUR, MSFT, USD, VBTLX, VTSAX
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

The repo ships three sample personas — synthetic ledgers you can
talk to without touching your real data. The bundle carries them
fully built; from a clone, [one command builds them](samples/README.md).
Pick one, point the server at it, and start asking questions.

### `samples/alex-chen-morales.gnucash` — Personal + freelance

A Seattle-based independent software contractor with a
single-member LLC and a spouse on a hospital payroll. USD-default.
111 accounts and over 2,000 transactions from 2025 to the build
date. Has a mortgage, a brokerage with VTSAX/VBTLX/AAPL/MSFT/ETH
holdings, a Solo 401(k) beside the spouse's 403(b), eight
customers invoiced in USD, EUR and CAD, a subcontractor billed
through A/P, Washington B&O tax, scheduled bills, budgets —
pretty much everything the server can do, all in one book.

### `samples/lin-wei.gnucash` — Shenzhen software studio

A Shenzhen developer running a registered sole-proprietor studio
that builds cross-border e-commerce software, with a spouse on a
hospital payroll. CNY-default, on a native zh_CN chart. 101
accounts and about 3,000 transactions. Shenzhen tech clients
(Tencent, DJI, SF Tech and others) paying in CNY, USD/EUR clients
paying in foreign currency with
realized FX gain/loss on rate moves, domestic Chinese investments
(宁德时代 and two ETFs), a part-time employee, an HKD credit card,
a mortgage, and mixed payment rails (corporate account + Alipay +
WeChat Pay).

### `samples/sabine-brenner.gnucash` — German freelancer, SKR03 chart

A Munich-based freelance designer. EUR-default, on a German
SKR03 chart of accounts — every account name in German. 125
accounts and about 1,900 transactions, with live VAT returns and a
company car under the 1% rule. If a feature assumes English
account names or USD, Sabine's book is where it breaks.

All three books are fictional. See
[samples/README.md](samples/README.md) for the full breakdown of
what's in each.

---

## Quick Start

### Install in one click (Claude Desktop)

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

That's the entire install.

### Try it

Ask Claude:

- "Summarize the book."
- "What's my net worth been doing?"
- "Show me anyone who owes me money."
- "What did I spend on dining last month?"
- "Set a $500 monthly grocery budget."

The first response usually starts with the dashboard from
above. Everything after that is conversational.

When you're ready for your own book, see
[Connecting to your own book](#connecting-to-your-own-book) below.

### Other AI clients, or installing from source

ChatGPT and Codex, Claude Code, Gemini CLI, Google Antigravity,
and any other MCP client connect through an install from a git
clone, as do database books and anyone working on the server:
see [docs/CLIENTS.md](docs/CLIENTS.md).

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

**GnuCash 3.8 or newer.** Once the server has written to a book, the
book carries a feature marker ("Use natural signs in budget
amounts") that GnuCash 3.8 introduced, and GnuCash 3.0–3.7 refuses
to open a book marked with a feature it does not know. GnuCash 3.8
and later mark any book with a budget the same way when they open
it, so this only matters if you still run an older 3.x. The server
is tested against GnuCash 5.12.

### Point the server at it

With the bundle, choose the book in the GnuCash extension's settings
in Claude Desktop, then restart Claude Desktop. With a clone install,
set `GNUCASH_BOOK_PATH` to the book's absolute path; see
[Using your own book](docs/CLIENTS.md#using-your-own-book).

### Or: keep the book in PostgreSQL or MySQL

GnuCash can also keep a book in a database instead of a file, and
the server serves one of those too. Point it at a connection
string instead of a path:

```json
{
  "command": "/Users/yourname/.local/bin/gnucash-mcp",
  "args": ["--modules=all"],
  "env": {
    "GNUCASH_BOOK_URI": "postgresql://user:password@localhost:5432/gnucash",
    "GNUCASH_LOG_DIR": "/Users/yourname/gnucash-mcp-logs"
  }
}
```

Install the driver alongside the server — `postgres` or `mysql`
(MariaDB uses the same one). From inside your clone (the
`gnucash-mcp` folder; see [installing from source](docs/CLIENTS.md)):

```bash
uv tool install -e ".[postgres]" --reinstall
```

For MySQL / MariaDB the connection string is
`mysql+pymysql://user:password@localhost:3306/gnucash` and the extra
is `[mysql]`: `uv tool install -e ".[mysql]" --reinstall`. The
shorter forms GnuCash itself writes, `mysql://` and `postgres://`,
work too; the server uses the driver the extra installed.

Install from the clone, as above, not by name: the name `gnucash-mcp`
on PyPI belongs to a different project.

To move an existing book across: open it in GnuCash,
**File → Save As**, pick **postgres** or **mysql**, and fill in the
connection details. (On Debian/Ubuntu those entries need `sudo apt
install libdbd-pgsql` or `libdbd-mysql`, the same way SQLite3 needs
`libdbd-sqlite3`; the macOS and Windows builds ship all three.)
**Keep the file** — it stays a perfectly good backup of everything
up to the moment you switched.

Worth knowing before you switch:

- **`GNUCASH_BOOK_PATH` and `GNUCASH_BOOK_URI` are mutually
  exclusive** — a book is a file or a database, and setting both
  is a startup error rather than a coin toss over which ledger
  your writes land in.
- **`GNUCASH_LOG_DIR` becomes required.** Audit and debug logs
  normally live in a folder beside the book file; a connection
  string has no "beside".
- **One book per server.** `switch_book` matches on filenames, so
  multi-book stays a file feature.
- **The server stops taking backups.** This is the real trade-off:
  the automatic safety net exists because it can snapshot a file,
  and it can't snapshot your database. `create_backup` says so
  rather than pretending. Set up `pg_dump` or `mysqldump` on a
  schedule before you move a real book over — see
  [`docs/RESTORE_FROM_BACKUP.md`](docs/RESTORE_FROM_BACKUP.md).
- Your password is masked wherever the server names the book — in
  tool results, in the dashboard header, and in the audit log — and
  in every error message, log line, and startup error, including
  the ones a database driver writes. A password given as a query
  parameter (`?password=…`, `sslpassword=…`) is masked the same
  way.
- **Put the connection string in the `env` block, not on the
  command line.** `--book-uri` works, but a password in a command
  line is visible to every user of the machine in the process
  list. On PostgreSQL you can also leave the password out of the
  string entirely and let the driver read `PGPASSWORD` or
  `~/.pgpass`.

Both dialects are exercised by the test suite and CI: PostgreSQL
16 and MariaDB 11, each against a real server.

---

## Choosing a module set

`--modules=all` is the easy default — every tool, 86 of them.
For day-to-day use you'll probably want less. Pick the role that
matches how you'll talk to the server; you can also pick the
modules behind each role individually for a finer cut.

| Role | What it gives you | Tools |
|---|---|---|
| `core` | Ledger primitives — accounts, transactions, balances, slots, audit log, backups, balance sheet, **reconciliation**. **Always loaded.** | 29 |
| `bookkeeper` | Everything except business: reports, budgets, scheduled transactions, prices, and investment lots. The personal-finance set. | 30 |
| `investor` | Cost-basis tracking and price management only (a subset of `bookkeeper`). | 13 |
| `business` | Customers, vendors, and employees; invoices, bills, vouchers, and credit notes; sales tax, payment terms, jobs, and vendor reports. | 27 |

Pick one or more, comma-separated:

```json
"args": ["--modules=bookkeeper"]            // personal finance
"args": ["--modules=investor"]              // self-directed investor
"args": ["--modules=business"]              // invoicing, freelance or small business
"args": ["--modules=bookkeeper,business"]   // everything (same as all)
```

`core` is added regardless. `freelancer` and `business_complete`,
the names 1.4 used, are still accepted and mean `business`. The
modules behind each role (`reconciliation`, `reporting`,
`budgets`, `scheduling`, `tax_lots`, `portfolio`, etc.) are
individually selectable too — run `uv run gnucash-mcp --help` from
the repo for the full menu.

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

If you open a server-posted invoice in GnuCash's Process Payment
dialog, set its "Post To" account to the invoice's receivable first:
the dialog lists only the documents posted to the selected account.

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

**Your book file never leaves your machine.** This server is a
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

**Automatic backups.** Before the first write of each session
(and again as a long-running session crosses into a new backup
period), the server snapshots your book to
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

## Known limitations

- **Don't edit in GnuCash desktop and through the server at the
  same time.** The server respects GnuCash's lock but doesn't hold
  one of its own (it opens the book for one call at a time), so
  GnuCash won't warn you that the server is using the book.
- **Books with "Use Trading Accounts" turned on:** transactions
  across currencies or commodities, including stock and fund
  purchases, are refused, because the server can't yet write
  trading splits the way GnuCash does. Enter those in GnuCash
  desktop; everything in a single currency works as usual.
- **GnuCash 3.8 or newer.** A book the server has written to
  carries a feature marker that GnuCash 3.0–3.7 can't open.
- **Foreign-currency spending and income** are valued at each
  month's closing rate in the spending and income reports, not at
  the cash that paid for them; `cash_flow` reports the cash.

The full list is in [CHANGELOG.md](CHANGELOG.md).

---

## Limiting what the AI can see

Each tool's description lives in the AI's system prompt, which
costs context on every message. Narrowing the toolset to what
you actually use makes every conversation cheaper. See
[choosing a module set](#choosing-a-module-set) above for the
four roles (`core`, `bookkeeper`, `investor`, `business`).

You can also set `GNUCASH_MCP_MODULES=core,bookkeeper` as an
environment variable instead of `--modules=...` in the JSON
args.

---

## What's new in v1.5.0

- **Books in a database.** Point the server at a book GnuCash keeps
  in PostgreSQL or MySQL/MariaDB, not just a SQLite file — see
  [Or: keep the book in PostgreSQL or MySQL](#or-keep-the-book-in-postgresql-or-mysql).
  PostgreSQL support was contributed by
  [@vchatela](https://github.com/vchatela).
- **Everything is stored the way GnuCash desktop stores it.**
  Scheduled transactions run in desktop's Since Last Run, budgets,
  invoices, credit notes, voids, and prices read the same in both,
  and invoice totals match GnuCash's own to the cent. Upgrading
  from 1.4? Read [the upgrade guide](docs/UPGRADING.md) first.
- **Prepayments.** Record a customer's or vendor's overpayment as
  money held for them, settle a later invoice from it, and unpost
  a paid invoice without losing the payment.
- **Num and document links** on every transaction tool, with the
  number used as a duplicate check.
- **A map of your chart of accounts** on the dashboard, so the
  assistant knows where every account lives before it asks.
- **Sample books built from source**, each checked against its own
  country's tax practice (US/Washington, Germany, China) and
  current to the day they're built.

Earlier releases are in [CHANGELOG.md](CHANGELOG.md).

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
  The server honors GnuCash's lock but deliberately takes none of
  its own (it holds the book for one call at a time), so GnuCash
  will open a book the server is using without a warning. Don't
  edit in both at once.
- Try opening the book in GnuCash itself to verify it isn't
  corrupted.

### Docker: "both GNUCASH_BOOK_PATH and GNUCASH_BOOK_URI are set"

The image ships with `GNUCASH_BOOK_PATH` pointing at its bundled
demo books. To serve a database book from it, clear that default
on the command line (`-e GNUCASH_BOOK_PATH=`) beside your
`GNUCASH_BOOK_URI`; to serve a mounted file, set
`GNUCASH_BOOK_PATH` to the mounted path and run the container as
the user who owns the file (`--user "$(id -u):$(id -g)"`).

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
