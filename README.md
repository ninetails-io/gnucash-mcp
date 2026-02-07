# gnucash-mcp

A Model Context Protocol (MCP) server for GnuCash, enabling AI assistants to read and write accounting data.

## Features

**25 tools** across six categories:

| Category | Tools | Description |
|----------|-------|-------------|
| Accounts | `list_accounts`, `list_commodities`, `get_account`, `get_balance`, `create_account`, `update_account`, `move_account`, `delete_account` | Full chart-of-accounts management with multi-currency support |
| Transactions | `list_transactions`, `get_transaction`, `create_transaction`, `update_transaction`, `delete_transaction`, `search_transactions`, `void_transaction`, `unvoid_transaction` | CRUD, search, cross-currency transactions, and proper accounting voids |
| Reconciliation | `set_reconcile_state`, `get_unreconciled_splits`, `reconcile_account` | Bank statement reconciliation workflow |
| Reporting | `spending_by_category`, `income_by_source`, `balance_sheet`, `net_worth`, `cash_flow` | Computed financial reports |
| Audit | `get_audit_log` | Read the server's own audit trail |

**Resources:**

- `gnucash://accounts` — Chart of accounts as JSON

## Requirements

- Python 3.10+
- GnuCash book saved in **SQLite format** (not XML)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

To convert an existing GnuCash book to SQLite: File → Save As → choose SQLite3.

## Installation

```bash
git clone https://github.com/ninetails-io/gnucash-mcp.git
cd gnucash-mcp
uv sync
```

## Configuration

Set the path to your GnuCash book:

```bash
export GNUCASH_BOOK_PATH="/path/to/your/book.gnucash"
```

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gnucash-mcp", "gnucash-mcp"],
      "env": {
        "GNUCASH_BOOK_PATH": "/path/to/your/book.gnucash"
      }
    }
  }
}
```

### Claude Code

Add to your `.mcp.json` or project settings:

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gnucash-mcp", "gnucash-mcp"],
      "env": {
        "GNUCASH_BOOK_PATH": "/path/to/your/book.gnucash"
      }
    }
  }
}
```

### Other MCP Clients

```bash
# Development mode (with MCP inspector)
uv run mcp dev src/gnucash_mcp/server.py

# Direct execution
uv run gnucash-mcp

# Or via module
uv run python -m gnucash_mcp
```

## Safety

- The server opens books in read-write mode — **back up your data** before first use
- Backup file creation by piecash is suppressed (the server opens/closes the book per operation, which would otherwise create a backup on every call)
- Split validation enforces balanced transactions (splits must sum to zero)
- Account deletion is blocked if the account has children or transactions
- Void is preferred over delete for transactions (preserves audit trail)

## Audit Logging

All write operations are logged to an audit trail stored alongside your book:

```
/path/to/book.gnucash.mcp/
  audit/YYYY-MM-DD.txt    # Human-readable audit log (default)
  debug/YYYY-MM-DD.log    # Debug log (when enabled)
```

**Formats:**
- `text` (default) — Human-readable, logs only write operations
- `json` — JSONL format, logs all operations including reads, supports filtering

**CLI flags:**
```bash
gnucash-mcp --debug                    # Enable debug logging
gnucash-mcp --noaudit                  # Disable audit logging
gnucash-mcp --audit-format=json        # Use JSON format
```

**Environment variables:**
- `GNUCASH_MCP_AUDIT_FORMAT=text|json`
- `GNUCASH_MCP_NOAUDIT=1`
- `GNUCASH_MCP_DEBUG=1`

## Project Structure

```
gnucash-mcp/
├── src/gnucash_mcp/
│   ├── __init__.py          # Package init
│   ├── __main__.py          # python -m entry point
│   ├── server.py            # MCP server, tool definitions, error handling
│   ├── book.py              # GnuCash book wrapper (piecash operations)
│   └── logging_config.py    # Audit and debug logging
├── tests/
│   ├── conftest.py          # Test fixtures (temp book creation)
│   ├── test_book.py         # Book wrapper tests
│   ├── test_tools.py        # MCP tool integration tests
│   └── test_logging.py      # Audit logging tests
├── pyproject.toml
├── LICENSE
└── README.md
```

## Development

```bash
uv sync                  # Install dependencies
uv run pytest            # Run tests
uv run pytest -x -v      # Run tests (stop on first failure, verbose)
```

## Roadmap

Implemented features cover accounts, transactions, reconciliation, reporting, and audit logging. Remaining backlog:

- [x] Multiple currency support
- [ ] Export transactions to CSV
- [ ] Import from CSV (with field mapping)
- [ ] Import from OFX/QFX
- [ ] Duplicate transaction detection

## License

[MIT](LICENSE)

## Acknowledgments

- [piecash](https://github.com/sdementen/piecash) — Python interface to GnuCash
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Model Context Protocol implementation
