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

Optional flags: `"--debug"` enables debug logging, `"--audit-format=text"` or `"--audit-format=json"` sets the audit log format. Add these after `"gnucash_mcp"` in the args array.

### Step 4: Restart Claude Desktop

Quit Claude Desktop completely and reopen it. Look for the hammer 🔨 icon — that means MCP tools are available.

### Step 5: Try it

Ask Claude:
- "List my accounts"
- "What's my checking account balance?"
- "What did I spend on dining this month?"

---

## What can it do?

**52 tools** across twelve categories:

| Category | What you can ask |
|----------|------------------|
| **Overview** | "Summarize the book", "What's the financial picture?", "Orient me" |
| **Accounts** | "List my accounts", "What's my checking balance?", "Create a new expense category for subscriptions" |
| **Transactions** | "Show recent transactions", "Record a $50 grocery purchase", "Find all Amazon transactions" |
| **Budgets** | "Create a monthly budget", "Set grocery budget to $500", "How am I doing on my budget?" |
| **Scheduled** | "Set up rent as a monthly bill", "What bills are coming up?", "Pay this month's electric bill" |
| **Investments** | "Track my VTSAX shares", "What's my cost basis?", "Calculate my capital gains" |
| **Reports** | "Spending by category last month", "What's my net worth?", "Show cash flow this year" |
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

This server **writes to your GnuCash book**. Before first use:

1. Make a copy of your `.gnucash` file
2. Consider using a test book first
3. The server logs all changes to an audit file (see Audit Logging below)

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
| `GNUCASH_MCP_AUDIT_FORMAT` | `text` (default) or `json` |

### Tool Modules

By default, only the **core** module (15 tools) is loaded to minimize context usage. Load additional modules as needed:

```bash
# Default: core only (15 tools — accounts, transactions, book summary)
gnucash-mcp

# All 52 tools
gnucash-mcp --modules=all

# Mix and match
gnucash-mcp --modules=core,reporting,reconciliation
```

| Module | Tools | Description |
|--------|-------|-------------|
| `core` | 15 | Accounts, transactions, book summary (always loaded) |
| `reconciliation` | 5 | Unreconciled splits, reconcile, void/unvoid |
| `reporting` | 5 | Spending, income, balance sheet, net worth, cash flow |
| `budgets` | 6 | Budget CRUD and variance reports |
| `scheduling` | 6 | Recurring transactions and upcoming bills |
| `investments` | 11 | Commodities, prices, lots, cost basis |
| `admin` | 4 | Account metadata slots, audit log |

When `--debug` is set, an additional `get_server_config` diagnostic tool is registered. It reports loaded modules, tool count, book path, debug mode, and version — useful for verifying what the server actually loaded.

### Claude Code

Run this command to add the server (use `--scope user` for all projects, or `--scope project` for the current project only):

```bash
claude mcp add-json gnucash \
  '{"command":"uv","args":["run","--directory","/path/to/gnucash-mcp","python","-m","gnucash_mcp"],"env":{"GNUCASH_BOOK_PATH":"/path/to/your/book.gnucash"}}'
```

Replace both paths with your actual paths. Add `"--modules=all"`, `"--debug"`, or `"--audit-format=text"` to the args array as needed.

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

## All 52 Tools

<details>
<summary>Click to expand full tool list</summary>

| Category | Tools |
|----------|-------|
| Overview | `get_book_summary` |
| Accounts | `list_accounts`, `get_account`, `get_balance`, `create_account`, `update_account`, `move_account`, `delete_account` |
| Commodities & Prices | `list_commodities`, `create_commodity`, `create_price`, `get_prices`, `get_latest_price` |
| Transactions | `list_transactions`, `get_transaction`, `create_transaction`, `update_transaction`, `replace_splits`, `delete_transaction`, `search_transactions`, `void_transaction`, `unvoid_transaction` |
| Reconciliation | `set_reconcile_state`, `get_unreconciled_splits`, `reconcile_account` |
| Reporting | `spending_by_category`, `income_by_source`, `balance_sheet`, `net_worth`, `cash_flow` |
| Budgets | `create_budget`, `list_budgets`, `get_budget`, `set_budget_amount`, `get_budget_report`, `delete_budget` |
| Scheduled Transactions | `create_scheduled_transaction`, `list_scheduled_transactions`, `get_upcoming_transactions`, `create_transaction_from_scheduled`, `update_scheduled_transaction`, `delete_scheduled_transaction` |
| Lots | `create_lot`, `list_lots`, `get_lot`, `assign_split_to_lot`, `calculate_lot_gain`, `close_lot` |
| Account Metadata | `get_account_slots`, `set_account_slot`, `delete_account_slot` |
| Audit | `get_audit_log` |

</details>

---

## Development

```bash
git clone https://github.com/ninetails-io/gnucash-mcp.git
cd gnucash-mcp
uv sync
uv run pytest           # Run tests
uv run pytest -x -v     # Stop on first failure, verbose
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
- [ ] CSV export
- [ ] CSV/OFX import

---

## License

[MIT](LICENSE)

## Acknowledgments

- [piecash](https://github.com/sdementen/piecash) — Python interface to GnuCash
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Model Context Protocol 