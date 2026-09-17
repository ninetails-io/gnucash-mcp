# Inspection/demo container for gnucash-mcp.
#
# This is NOT the recommended install for users — see README.md
# Quick Start (clone + uv, no build step). This image exists so
# automated MCP registries (e.g. Glama) can start the server and
# introspect its tools: it bundles the Alex Chen-Morales sample
# book and points GNUCASH_BOOK_PATH at it, satisfying the
# fail-fast startup check with a realistic, disposable book.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Dependencies first, so they cache independently of source edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Project source + the three sample books: multi-book mode
# registers the conditional switch_book tool, so the full 86-tool
# surface is introspectable (Alex: USD freelancer; Lin Wei: CNY
# multi-currency; Sabine: EUR, German SKR03 chart).
COPY README.md ./
COPY src ./src
COPY samples/alex-chen-morales.gnucash samples/lin-wei.gnucash samples/sabine-brenner.gnucash ./samples/
RUN uv sync --frozen --no-dev

# Generate the demo books through the build date, the way CI does
# for the MCPB bundle: samples/ holds chart-only bases, and the
# builders regenerate every transaction from them deterministically,
# priced from the committed offline rates cache — no network. The
# /tmp promotion backups are build-layer junk; drop them.
COPY scripts/synthetic_book ./scripts/synthetic_book
RUN uv run --no-sync python scripts/synthetic_book/rebuild_all.py --skip-refresh --force \
    && rm -rf /tmp/sample-books-pre-rebuild-*

ENV GNUCASH_BOOK_PATH=/app/samples/alex-chen-morales.gnucash:/app/samples/lin-wei.gnucash:/app/samples/sabine-brenner.gnucash

# MCP stdio transport: the client (or registry checker) talks
# JSON-RPC over stdin/stdout.
CMD ["uv", "run", "--no-sync", "gnucash-mcp", "--modules=all"]
