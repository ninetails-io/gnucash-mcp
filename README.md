# gnucash-mcp

A Model Context Protocol (MCP) server for GnuCash, enabling AI assistants to read and write accounting data.

## Overview

This MCP server provides tools for interacting with GnuCash books stored in SQLite format. It uses [piecash](https://github.com/sdementen/piecash) for GnuCash file access and the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) for the protocol implementation.

## Features

### Tools

- `list_accounts` - List all accounts in the chart of accounts
- `get_account` - Get details for a specific account
- `get_balance` - Get current or historical balance for an account
- `list_transactions` - List transactions with optional filters (account, date range, limit)
- `get_transaction` - Get details for a specific transaction
- `create_transaction` - Create a new transaction with splits
- `search_transactions` - Search transactions by description, payee, or amount

### Resources

- `gnucash://accounts` - Chart of accounts
- `gnucash://balance/{account_name}` - Account balance

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

- [ ] Core tools (list, get, create transactions)
- [ ] Balance queries
- [ ] Search functionality
- [ ] Reporting tools (spending by category, income by source)
- [ ] Duplicate detection
- [ ] Reconciliation support
- [ ] Import from CSV/OFX

## License

MIT

## Acknowledgments

- [piecash](https://github.com/sdementen/piecash) - Python interface to GnuCash
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - Model Context Protocol implementation
- Built for use with [Claude](https://claude.ai) and the Project Clyde pantheon

---

*Born for the GnuCash dread* — Abe Raham, The Accountant
