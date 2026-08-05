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

# Project source + two sample books: multi-book mode registers the
# conditional switch_book tool, so the full 111-tool surface is
# introspectable (Alex: USD freelancer; Lin Wei: CNY multi-currency).
COPY README.md ./
COPY src ./src
COPY samples/alex-chen-morales.gnucash samples/lin-wei.gnucash ./samples/
RUN uv sync --frozen --no-dev

ENV GNUCASH_BOOK_PATH=/app/samples/alex-chen-morales.gnucash:/app/samples/lin-wei.gnucash

# MCP stdio transport: the client (or registry checker) talks
# JSON-RPC over stdin/stdout.
CMD ["uv", "run", "--no-sync", "gnucash-mcp", "--modules=all"]
