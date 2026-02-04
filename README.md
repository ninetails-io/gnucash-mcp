# gnucash-mcp

A Model Context Protocol (MCP) server for GnuCash, enabling AI assistants to read and write accounting data.

## Project Status

**Current Phase:** Core functionality complete, extending capabilities

| Category | Status |
|----------|--------|
| Core Read Tools | ✅ Complete |
| Core Write Tools | ✅ Complete |
| Account Management | ✅ Complete |
| Reconciliation | ✅ Complete |
| Void/Unvoid | ✅ Complete |
| Reporting | ✅ Complete |
| Import/Export | 🔲 Backlog |

## Overview

This MCP server provides tools for interacting with GnuCash books stored in SQLite format. It uses [piecash](https://github.com/sdementen/piecash) for GnuCash file access and the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) for the protocol implementation.

## Features

### Implemented Tools

**Account Operations:**
- `list_accounts` - List all accounts in the chart of accounts
- `get_account` - Get details for a specific account
- `get_balance` - Get current or historical balance for an account
- `create_account` - Create a new account in the chart of accounts
- `update_account` - Rename account, update description or placeholder status
- `move_account` - Move account to a new parent in the hierarchy
- `delete_account` - Delete account (with safeguards for children/transactions)

**Transaction Operations:**
- `list_transactions` - List transactions with optional filters (account, date range, limit)
- `get_transaction` - Get details for a specific transaction by GUID
- `create_transaction` - Create a new transaction with splits
- `update_transaction` - Update description, date, or splits of existing transaction
- `delete_transaction` - Delete a transaction by GUID
- `search_transactions` - Search transactions by description, memo, or amount
- `void_transaction` - Void a transaction (preserves audit trail)
- `unvoid_transaction` - Restore a voided transaction

**Reconciliation:**
- `set_reconcile_state` - Set split state (new/cleared/reconciled)
- `get_unreconciled_splits` - List unreconciled splits for an account
- `reconcile_account` - Batch reconcile with statement balance validation

**Reporting:**
- `spending_by_category` - Expense breakdown by category for a period
- `income_by_source` - Income breakdown by source for a period
- `balance_sheet` - Assets, liabilities, equity at a point in time
- `net_worth` - Calculate net worth (point-in-time or time series)
- `cash_flow` - Inflows and outflows for a period

### Resources

- `gnucash://accounts` - Chart of accounts as JSON

## Requirements

- Python 3.10+
- GnuCash book in SQLite format (not XML)
- Dependencies:
  - `mcp[cli]` >= 1.0.0
  - `piecash` >= 1.2.0

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/gnucash-mcp.git
cd gnucash-mcp

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e .
```

## Configuration

Set the path to your GnuCash book:

```bash
export GNUCASH_BOOK_PATH="/path/to/your/book.gnucash"
```

Or pass it as an argument when running the server.

## Usage

### Development Mode

```bash
uv run mcp dev src/gnucash_mcp/server.py
```

### Install in Claude Desktop

```bash
uv run mcp install src/gnucash_mcp/server.py --name "GnuCash"
```

### Direct Execution

```bash
python -m gnucash_mcp
```

## Project Structure

```
gnucash-mcp/
├── src/
│   └── gnucash_mcp/
│       ├── __init__.py
│       ├── server.py      # MCP server definition
│       ├── book.py        # GnuCash book wrapper
│       └── tools.py       # Tool implementations
├── tests/
├── pyproject.toml
└── README.md
```

## Safety

- The server opens GnuCash books in read-write mode by default
- piecash automatically creates backups before modifications
- Consider using a copy of your book for testing

## Roadmap

### ✅ Completed

- [x] Core read tools (list/get accounts, transactions, balances)
- [x] Transaction creation with split validation
- [x] Transaction update and delete
- [x] Search by description, memo, or amount
- [x] Account creation
- [x] Hardened error handling throughout
- [x] Reconciliation tools (set state, get unreconciled, batch reconcile)
- [x] Void/unvoid transactions
- [x] Account management (update, move, delete with safeguards)
- [x] Reporting (spending, income, balance sheet, net worth, cash flow)

### 🔲 Backlog

**Transaction Operations:**
- [ ] Duplicate transaction

**Import/Export:**
- [ ] Export transactions to CSV
- [ ] Export account register to CSV
- [ ] Import from CSV (with field mapping)
- [ ] Import from OFX/QFX

**Advanced:**
- [ ] Duplicate transaction detection
- [ ] Scheduled transactions (if piecash supports full CRUD)

## License

MIT

## Acknowledgments

- [piecash](https://github.com/sdementen/piecash) - Python interface to GnuCash
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - Model Context Protocol implementation
- Built for use with [Claude](https://claude.ai) and the Project Clyde pantheon

---

*Born for the GnuCash dread* — Abe Raham, The Accountant
