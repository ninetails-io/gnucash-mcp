# gnucash-mcp

Talk to your GnuCash data through Claude.

```
You: What did I spend on groceries last month?

Claude: You spent $692.10 on groceries in January, across 12 transactions.
        The largest was $127.43 at Whole Foods on Jan 15.
```

```
You: How am I doing on my budget?

Claude: February budget status:
        • Groceries: $327 of $500 (65%) — $173 remaining
        • Dining: $245 of $200 (122%) — $45 over budget ⚠️
        • Entertainment: $45 of $100 (45%) — on track
```

```
You: What bills are coming up?

Claude: Next 14 days:
        • Feb 10: Electric (~$85)
        • Feb 15: Car Insurance ($156)
        • Feb 28: Internet ($75)
        Total: $316
```

## Quick Start (5 minutes)

### Step 1: Convert your GnuCash book to SQLite

**This is required.** The server only works with SQLite format, not XML.

In GnuCash:
1. File → Save As
2. Change "Data Format" to **SQLite3**
3. Save with a new filename (e.g., `mybook-sqlite.gnucash`)

Keep your original XML file as a backup.

### Step 2: Install

```bash
git clone https://github.com/ninetails-io/gnucash-mcp.git
cd gnucash-mcp
```

**With uv (recommended):**
```bash
uv sync
```

**With pip:**
```bash
pip install -e .
```

### Step 3: Configure Claude Desktop

Find your config file:
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add this (replace the paths with your actual paths):

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/gnucash-mcp",
        "python",
        "-m",
        "gnucash_mcp"
      ],
      "env": {
        "GNUCASH_BOOK_PATH": "/path/to/your/book.gnucash"
      }
    }
  }
}
```

If you used pip instead of uv:
```json
{
  "mcpServers": {
    "gnucash": {
      "command": "gnucash-mcp",
      "env": {
        "GNUCASH_BOOK_PATH": "/path/to/your/book.gnucash"
      }
    }
  }
}
```

#### Choosing modules (important)

**Only load the modules you actually use.** The server has 75 tools across 8 modules, but every loaded tool gets described in the system prompt — eating tokens and context on every single message. Most users only need 2-3 modules.

Add `--modules=` after `"gnucash_mcp"` in the args array with a comma-separated list:

```json
"args": [
  "run", "--directory", "/path/to/gnucash-mcp",
  "python", "-m", "gnucash_mcp",
  "--modules=core,reporting,budgets"
]
```

| Module | Tools | What it does |
|--------|-------|--------------|
| `core` | 15 | Accounts, transactions, book summary (always loaded) |
| `reconciliation` | 5 | Bank reconciliation, void/unvoid |
| `reporting` | 5 | Spending, income, balance sheet, net worth, cash flow |
| `budgets` | 6 | Budget creation, targets, variance reports |
| `scheduling` | 6 | Recurring transactions, upcoming bills |
| `investments` | 11 | Commodities, prices, lots, cost basis tracking |
| `business` | 22 | Customers, vendors, invoices, bills, payments |
| `admin` | 4 | Account metadata slots, audit log |
| `debug` | 1 | `get_server_config` — loaded when `--debug` is set |

Use `--modules=all` to load everything (75 tools), but only if you need it. You can also set `GNUCASH_MCP_MODULES=core,reporting` as an environment variable instead of using the CLI flag.

Other optional flags: `"--debug"` enables debug logging, `"--noaudit"` disables the audit log.

### Step 4: Restart Claude Desktop

Quit Claude Desktop completely and reopen it. Look for the hammer 🔨 icon — that means MCP tools are available.

### Step 5: Try it

Ask Claude:
- "List my accounts"
- "What's my checking account balance?"
- "What did I spend on dining this month?"

---

## What can it do?

**75 tools** across eleven categories:

| Category | What you can ask |
|----------|------------------|
| **Overview** | "Summarize the book", "What's the financial picture?", "Orient me" |
| **Accounts** | "List my accounts", "What's my checking balance?", "Create a new expense category for subscriptions" |
| **Transactions** | "Show recent transactions", "Record a $50 grocery purchase", "Find all Amazon transactions" |
| **Budgets** | "Create a monthly budget", "Set grocery budget to $500", "How am I doing on my budget?" |
| **Scheduled** | "Set up rent as a monthly bill", "What bills are coming up?", "Pay this month's electric bill" |
| **Investments** | "Track my VTSAX shares", "What's my cost basis?", "Calculate my capital gains" |
| **Reports** | "Spending by category last month", "What's my net worth?", "Show cash flow this year" |
| **Business** | "Create a customer", "Invoice Acme Corp for consulting", "Record payment on invoice", "How much did we spend with each vendor?" |
| **Multi-currency** | "Record a €50 purchase", "What's my EUR account balance?" |
| **Reconciliation** | "Show unreconciled transactions", "Mark these as cleared" |
| **Account Metadata** | "Set my credit card APR", "What metadata is on this account?" |

---

## Example Conversations

### Tracking expenses
```
You: I spent $47.50 at Safeway today on groceries, paid with my Visa.

Claude: Recorded: $47.50 from Credit Card:Visa to Expenses:Groceries
        on Feb 7, 2026. Description: "Safeway"
```

### Setting up recurring bills
```
You: Set up my rent as a recurring bill. $1,850 on the 1st of every month
     from checking.

Claude: Created scheduled transaction "Monthly Rent":
        • $1,850 monthly
        • From: Assets:Checking
        • To: Expenses:Rent  
        • Next due: March 1, 2026
```

### Investment tracking
```
You: I bought $1,000 of VTSAX at $125/share in my 401k.

Claude: Recorded VTSAX purchase:
        • 8 shares at $125.00/share
        • Cost basis: $1,000.00
        • Account: Assets:Investments:401k:VTSAX
        
        I've created a lot to track this purchase for capital gains.
```

### Invoicing a customer
```
You: Create a customer called "Acme Corp" and invoice them $1,500 for consulting.

Claude: Created customer "Acme Corp" (ID: 000001)
        Created invoice 000001:
        • 1x Consulting at $1,500.00
        • Total: $1,500.00

        Ready to post when you want to make it official.
```

### Tracking vendor spending
```
You: How much did we spend with each vendor this year?

Claude: Vendor spending (Jan–Feb 2026):
        • Office Depot: $2,340 (4 bills, $0 outstanding)
        • CloudHost Inc: $1,200 (2 bills, $600 outstanding)
        • Legal Associates: $3,500 (1 bill, $3,500 outstanding)
        Total billed: $7,040 | Paid: $2,940 | Outstanding: $4,100
```

### Month-end review
```
You: Give me a financial summary for January.

Claude: January 2026 Summary:
        
        Income: $7,149
        • Salary: $5,200
        • Spouse Salary: $1,749
        • VA Benefits: $200
        
        Expenses: $9,014
        • Rent: $1,850
        • Groceries: $692
        • Dining: $351
        • Credit Card Interest: $247
        ...
        
        Net: -$1,865 (spent more than earned)
        
        Credit card debt: $14,345
```

---

## Important: Back Up Your Data

This server **reads and writes your GnuCash book directly**. Your GnuCash file may contain your entire financial history — treat it accordingly.

1. **Back up your `.gnucash` file before first use** and keep regular backups
2. **Test with a copy first**, not your real book
3. All write operations are logged to an audit file (see Audit Logging below)

> **Disclaimer:** This software is provided "as is" under the [MIT License](LICENSE), without warranty of any kind. The authors are not liable for any data loss, corruption, or financial discrepancy arising from its use. You are solely responsible for maintaining backups and verifying the accuracy of your financial data.

---

## Troubleshooting

### "Tool not found" or no hammer icon
- Restart Claude Desktop completely (Quit, not just close)
- Check that your paths in the config file are correct
- Make sure the config file is valid JSON (no trailing commas)

### "Book not found" or path errors
- Use absolute paths, not `~` or relative paths
- On Mac/Linux: `/Users/yourname/Documents/book.gnucash`
- On Windows: `C:\\Users\\yourname\\Documents\\book.gnucash` (double backslashes in JSON)

### "Cannot open book" or piecash errors
- Make sure your book is SQLite format, not XML
- Check that GnuCash isn't currently open with the same book
- Try opening the book in GnuCash to verify it's not corrupted

### "Account not found"
- Use full account paths: `Expenses:Groceries`, not just `Groceries`
- Run "list my accounts" to see exact account names

### Something went wrong with my data
- Check the audit log in `[your-book].gnucash.mcp/audit/`
- Every write operation is logged with before/after states
- You can restore from your backup

---

## Configuration Reference

### Environment Variables

| Variable | Description |
|----------|-------------|
| `GNUCASH_BOOK_PATH` | Path to your GnuCash SQLite book (required) |
| `GNUCASH_MCP_MODULES` | Tool modules to load (e.g., `core,reporting`). Default: `core` |
| `GNUCASH_MCP_DEBUG` | Set to `1` for debug logging |
| `GNUCASH_MCP_NOAUDIT` | Set to `1` to disable audit logging |

### Tool Modules

See [Choosing modules](#choosing-modules-important) in the Quick Start for the full module table. From the command line:

```bash
# Default: core only (15 tools)
gnucash-mcp

# Load specific modules
gnucash-mcp --modules=core,reporting,business

# Everything (75 tools — not recommended unless you need it all)
gnucash-mcp --modules=all
```

When `--debug` is set, an additional `get_server_config` diagnostic tool is registered. It reports loaded modules, tool count, book path, debug mode, and version — useful for verifying what the server actually loaded.

### Claude Code

Run this command to add the server (use `--scope user` for all projects, or `--scope project` for the current project only):

```bash
claude mcp add-json gnucash \
  '{"command":"uv","args":["run","--directory","/path/to/gnucash-mcp","python","-m","gnucash_mcp"],"env":{"GNUCASH_BOOK_PATH":"/path/to/your/book.gnucash"}}'
```

Replace both paths with your actual paths. Add `"--modules=core,reporting"` (or whichever modules you need), `"--debug"`, or `"--noaudit"` to the args array.

### Other MCP Clients

```bash
# Set the book path
export GNUCASH_BOOK_PATH="/path/to/your/book.gnucash"

# Run directly
uv run gnucash-mcp

# Or with pip install
gnucash-mcp

# Development mode (with MCP inspector)
uv run mcp dev src/gnucash_mcp/server.py
```

---

## Audit Logging

All write operations are logged automatically:

```
/path/to/book.gnucash.mcp/
  audit/YYYY-MM-DD.txt    # What changed and when
  debug/YYYY-MM-DD.log    # Debug info (when enabled)
```

Example audit entry:
```
2026-02-07 14:32:15 | create_transaction | WRITE
  description: Safeway
  date: 2026-02-07
  splits: Expenses:Groceries $47.50, Liabilities:Credit Card:Visa -$47.50
  guid: a1b2c3d4...
```

---

## All 82 Tools

<details>
<summary>Click to expand full tool list</summary>

| Category | Tools |
|----------|-------|
| Overview | `get_book_summary` |
| Accounts | `list_accounts`, `get_account`, `get_balance`, `create_account`, `update_account`, `move_account`, `delete_account` |
| Commodities & Prices | `list_commodities`, `create_commodity`, `create_price`, `get_prices`, `get_latest_price` |
| Transactions | `list_transactions`, `get_transaction`, `create_transaction`, `update_transaction`, `replace_splits`, `delete_transaction`, `search_transactions`, `void_transaction`, `unvoid_transaction` |
| Reconciliation | `set_reconcile_state`, `get_unreconciled_splits`, `reconcile_account` |
| Reporting | `spending_by_category`, `income_by_source`, `balance_sheet`, `net_worth`, `cash_flow`, `debt_payoff_plan` |
| Budgets | `create_budget`, `list_budgets`, `get_budget`, `set_budget_amount`, `get_budget_report`, `delete_budget` |
| Scheduled Transactions | `create_scheduled_transaction`, `list_scheduled_transactions`, `get_upcoming_transactions`, `create_transaction_from_scheduled`, `update_scheduled_transaction`, `delete_scheduled_transaction` |
| Lots | `create_lot`, `list_lots`, `get_lot`, `assign_split_to_lot`, `calculate_lot_gain`, `close_lot` |
| Business | `create_customer`, `list_customers`, `get_customer`, `delete_customer`, `create_vendor`, `list_vendors`, `get_vendor`, `delete_vendor`, `create_employee`, `list_employees`, `get_employee`, `delete_employee`, `create_billterm`, `list_billterms`, `create_invoice`, `create_bill`, `add_invoice_entry`, `add_bill_entry`, `list_invoices`, `get_invoice`, `post_invoice`, `pay_invoice`, `delete_invoice`, `delete_bill`, `get_outstanding_invoices`, `vendor_spending_report` |
| Account Metadata | `get_account_slots`, `set_account_slot`, `delete_account_slot` |
| Backups | `create_backup`, `list_backups`, `prune_backups` |
| Audit | `get_audit_log` |
| Debug | `get_server_config` (loaded when `--debug` is set) |

</details>

---

## Development

```bash
git clone https://github.com/ninetails-io/gnucash-mcp.git
cd gnucash-mcp
uv sync --extra dev
uv run pytest           # Run tests
uv run pytest -x -v     # Stop on first failure, verbose
uv run ruff check src/ tests/     # Lint
uv run black --check src/ tests/  # Formatter check (no reformat)
```

### Project Structure

```
gnucash-mcp/
├── src/gnucash_mcp/
│   ├── server.py            # MCP server and tool definitions
│   ├── book.py              # GnuCash operations (piecash wrapper)
│   └── logging_config.py    # Audit logging
├── tests/
├── docs/                    # Design specs
└── README.md
```

---

## Changelog

### v1.2.1 — Backups, Employees, Efficiency

Protection, the third business entity, and the biggest efficiency pass
since v1.0.2. The server now snapshots the book before the first write
of every session, Employees join Customers and Vendors as a first-class
entity, and most read and write paths were measured and optimized.

- **Automatic backups**: The first write of each server process
  triggers an SQLite online-backup snapshot with grandfather-father-son
  retention (7 session / 4 weekly / 6 monthly automatic, unbounded
  manual). Every snapshot is verified with `PRAGMA integrity_check`
  before being declared valid. Data loss is now recoverable from
  within the server's own directory — no OS-level backup required.
- **Manual backup tools**: `create_backup(label)`, `list_backups`,
  `prune_backups(dry_run=true)`. Restore is deliberately *not* a tool
  — it's a documented filesystem procedure (see
  [RESTORE_FROM_BACKUP.md](docs/RESTORE_FROM_BACKUP.md)) performed
  with the server stopped.
- **Employees**: Third business-person entity alongside Customer and
  Vendor. `create_employee`, `list_employees`, `get_employee`,
  `delete_employee`. Expense vouchers are out of scope for this release.
- **Register-form compact output**: `list_transactions(account=X)`
  now renders in bank-register form — `DATE  guid  ±amount  desc
  other-splits` — with the signed impact on the filtered account in
  column 3. Fixes a reported confusion where the earlier format made
  the description look like it blended into the remaining split.
- **Split-list collapse**: Transactions with more than 4 splits in
  the rendered column render the top 3 by |value| plus `+N more`.
  A 17-split paycheck now fits on one legible line; call
  `get_transaction(guid)` for the full breakdown when needed.
- **Short collision-safe GUIDs in write responses**: Write tools now
  return an 8-character GUID prefix (extended only when the birthday
  problem actually bites at scale) instead of the full 32-char hex.
  Tools that accept GUIDs accept the prefix seamlessly via
  `_resolve_guid`. Large savings on every follow-up call.
- **Thin write responses**: `update_transaction`, `replace_splits`,
  and many other write tools now return only the new information the
  caller couldn't already know. The audit log pulls missing fields
  from tool parameters to preserve full before/after diff detail in
  the human-readable trail.
- **Single book-open per write**: The audit decorator used to open
  the book twice per write (once to capture before-state, once for
  the actual write). Now write methods stage their before-state
  inside the same session they already have open, halving write
  latency.
- **SQL-pushed reporting**: `spending_by_category`, `income_by_source`,
  `balance_sheet`, `net_worth`, and `cash_flow` now push date, account
  type, and account GUID filters into SQLite rather than scanning
  `book.transactions` in Python. Aggregation stays in Python to
  preserve exact `Decimal` arithmetic.
- **Cumulative-sum `net_worth` series**: A 60-month net-worth
  time series used to be O(intervals × splits); now it's O(splits
  + intervals) via a single sorted sweep.
- **Business module DRY refactor**: Create and delete paths for
  Customer, Vendor, and Employee share a single implementation each
  — parameterized on the piecash class and small config / callback
  parameters. Identical behavior, substantially less duplication.
- **Audit log dispatcher**: The text-format audit log's 380-line
  if/elif chain was flattened into a dispatch table keyed on
  `(entity_type, operation)`. New entity types are now a dict entry,
  not another elif branch.
- **Structured deletes**: Every raw `text("DELETE FROM ...")` site in
  the codebase was replaced with SQLAlchemy Core
  (`Table.__table__.delete().where(...)`) with post-delete
  verification. Future GnuCash schema changes surface as loud import
  errors rather than silent runtime failures.
- **Linter & formatter config**: `ruff` and `black` sections added
  to `pyproject.toml`. No bulk reformat in this release — style
  drift gets fixed on the next touch to each file, when it's cheapest.
- **Version**: 1.2.1 (701 tests)

### v1.2.0 — Business Module

Full accounts receivable and accounts payable workflow. Create customers and vendors, generate invoices and bills, post them to A/R or A/P, and record payments — all through natural language.

- **Customers & vendors**: Create, list, and query customers and vendors with addresses
- **Billing terms**: Define payment terms (e.g., Net 30 with early payment discounts)
- **Invoices & bills**: Create invoices for customers and bills from vendors, add line items, post to A/R or A/P accounts, record full or partial payments
- **Reporting**: List outstanding invoices/bills, vendor spending breakdown by period
- **GnuCash UI compatibility**: Posted invoices include metadata slots (`gncInvoice`, `trans-date-due`, `date-posted`) so they display correctly in the native GnuCash interface
- **Write verification**: All raw SQL operations (those bypassing the piecash ORM) are now verified with a read-back check before commit, with automatic rollback on failure
- **`business` tool module**: 22 tools, loaded via `--modules=business` or `--modules=all`
- **Server-level MCP instructions**: The server now sends structured accounting guidance (double-entry basics, workflow conventions, safety rules) to clients at connection time via the MCP `instructions` field — helping non-Claude models use the tools correctly without bloating individual tool descriptions
- **Version**: 1.2.0 (540 tests)

### v1.1.0 — Modular Tool Loading

The context-efficiency release. Previous versions advertised all tools to every client, consuming system prompt tokens whether you needed investments or not.

- **Tool modules** (`--modules=`): Load only the tool categories you need. Seven modules (core, reconciliation, reporting, budgets, scheduling, investments, admin) let you go from 52 tools down to as few as 15. Core is always loaded; `all` loads everything.
- **`get_server_config` debug tool**: When `--debug` is set, a diagnostic tool reports loaded modules, tool count, book path, and version. Clients can verify their own inventory instead of guessing.
- **`GNUCASH_MCP_MODULES` env var**: Configure modules without CLI flags — useful for Claude Desktop configs.
- **Version**: 1.1.0 (424 tests)

### v1.0.2 — Compact Output

Reduced token usage on the *response* side. Every read tool that returned verbose JSON by default now returns compact one-line-per-item text instead.

- **Compact default output** for list_transactions, list_commodities, list_scheduled_transactions, get_unreconciled_splits, list_lots — verbose JSON available via `verbose=true`
- **`get_book_summary`**: Single-call financial snapshot — book path, account structure, key balances, net worth, commodities, and scheduled transactions in one text response
- **Minified JSON**: Stripped null/empty values and whitespace from all JSON responses
- **Partial GUID support**: 8+ character prefixes accepted for transactions, splits, lots, and scheduled transactions
- **Version**: 1.0.2 (399 tests)

### v1.0.0 — Stable Release

Feature-complete with write safety and audit trail.

- **`replace_splits`**: Wholesale split replacement on existing transactions (recategorization without void/recreate)
- **Transaction pipeline**: Duplicate detection, dry run mode, auto-fill from prior transactions, date sanity checks, placeholder account warnings
- **`list_accounts` compact mode**: One-line-per-account default with `root` filter
- **Account metadata slots**: Custom key-value pairs on accounts (APR, credit limits, reward rates)
- **Audit log text format**: Human-readable audit trail alongside JSON option
- **Version**: 1.0.0 (394 tests)

### v0.9.0 — Feature Build-out

From basic CRUD to a full accounting toolkit.

- **Investments**: Commodities, prices, lot-based cost basis tracking, capital gain calculation
- **Scheduled transactions**: Recurring templates, upcoming bills, one-click instantiation
- **Budgets**: Create budgets, set targets by period/quarter, variance reporting
- **Multi-currency**: Cross-currency transactions with quantity/value split handling
- **Reporting**: Spending by category, income by source, balance sheet, net worth, cash flow
- **Reconciliation**: Statement reconciliation, void/unvoid with audit trail
- **Audit logging**: Automatic write-operation logging alongside the book file
- **Version**: 0.9.0 (187 tests)

### v0.1.0 — Initial Release

- Account listing, balances, transaction CRUD, search
- MCP server with FastMCP, Claude Desktop integration
- piecash SQLite interface with error handling

---

## Roadmap

- [x] Full account management
- [x] Transaction CRUD with search
- [x] Multi-currency support
- [x] Investment tracking with cost basis
- [x] Budgets with variance reporting
- [x] Scheduled transactions
- [x] Audit logging
- [x] Split recategorization (`replace_splits`)
- [x] Compact output for reduced token usage
- [x] Partial GUID support (8+ character prefixes)
- [x] Duplicate detection (built into `create_transaction`)
- [x] Modular tool loading (`--modules=`)
- [x] Business: customers, vendors, invoices, bills, payments
- [x] Write verification for raw SQL operations
- [x] Server-level MCP instructions for non-Claude clients
- [ ] Business: employees
- [ ] CSV export
- [ ] CSV/OFX import

---

## Support the Project

If gnucash-mcp is useful to you, consider [buying me a coffee](https://ko-fi.com/gomezfox). It helps keep development going.

---

## License

[MIT](LICENSE)

## Acknowledgments

- [piecash](https://github.com/sdementen/piecash) — Python interface to GnuCash
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Model Context Protocol 