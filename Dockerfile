# Inspection/demo container for gnucash-mcp.
#
# This is NOT the recommended install for users — see README.md
# Quick Start (clone + uv, no build step). This image exists so
# automated MCP registries (e.g. Glama) can start the server and
# introspect its tools: it builds the three sample books and
# points GNUCASH_BOOK_PATH at them, satisfying the fail-fast
# startup check with realistic, disposable books.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Dependencies first, so they cache independently of source edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Project source.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Build the three sample books through the build date, the way CI
# does for the MCPB bundle: the builders create each book from
# nothing, deterministically, priced from the committed offline
# rates cache (no network). Three books put the server in multi-book
# mode, so switch_book registers and the full tool surface is
# introspectable (Alex: USD freelancer; Lin Wei: CNY multi-currency;
# Sabine: EUR, German SKR03 chart). The /tmp promotion backups are
# build-layer junk; drop them.
COPY scripts/synthetic_book ./scripts/synthetic_book
RUN uv run --no-sync python scripts/synthetic_book/rebuild_all.py --skip-refresh --force \
    && rm -rf /tmp/sample-books-pre-rebuild-*

# Run as an ordinary user, not root. The user owns /app because the
# server writes there: each demo book's audit log and backups go in
# a folder beside it. To serve a book mounted from the host instead,
# run the container as the user who owns that file, e.g.
#   docker run --user "$(id -u):$(id -g)" -v "$PWD:/books" \
#     -e GNUCASH_BOOK_PATH=/books/my.gnucash ...
# The server is started from the virtualenv directly, so it needs no
# writable home directory under whatever user runs it.
RUN useradd --create-home --uid 10001 gnucash \
    && chown -R gnucash:gnucash /app
USER gnucash

ENV GNUCASH_BOOK_PATH=/app/samples/alex-chen-morales.gnucash:/app/samples/lin-wei.gnucash:/app/samples/sabine-brenner.gnucash

# MCP stdio transport: the client (or registry checker) talks
# JSON-RPC over stdin/stdout.
CMD ["/app/.venv/bin/gnucash-mcp", "--modules=all"]
