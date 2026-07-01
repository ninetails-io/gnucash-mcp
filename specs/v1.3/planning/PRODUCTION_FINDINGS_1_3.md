# Production Findings — v1.3 cycle

**Source:** Live accounting sessions, April–May 2026
**Status:** Archived — all items shipped
**Original home:** `specs/NEXT_STEPS_1_3.md` (retired, see archive
                   convention)

The bookkeeper review loop (live MCP usage against Alex Chen-Morales
and Lin Wei test books) surfaced three concrete UX findings during
the April–May 2026 production sessions. All three shipped in v1.3
Stages 2–3. This file preserves the original framing as written
contemporaneously so future contributors have the production
context if they ever want to trace back from a shipped feature to
the underlying user signal.

---

## `reconcile_account` bulk mode

**Original problem statement:**
Every reconciliation today requires two tool calls and a large token
payload. The LLM calls `get_unreconciled_splits` to get GUIDs, then
passes the entire GUID array back to `reconcile_account`. For a
checking account with 108 unreconciled splits, the GUID array alone
costs ~300 tokens — just echoing the server's own data back to it.

In production accounting (April–May 2026 session), every
reconciliation was all-or-nothing: reconcile every unreconciled
split against the statement balance. The partial-selection
capability was never used.

**Original plan:**
Add `reconcile_all=True` parameter to `reconcile_account`. When set,
the server internally fetches all unreconciled splits, sums them
against the existing reconciled balance, and verifies the total
matches `statement_balance`. If it ties, reconcile all splits in one
operation. If it doesn't, reject with the discrepancy amount so the
LLM knows what's missing.

Keep the existing `split_guids` parameter for partial reconciliation
— it's the right design when statement and book disagree and the
LLM needs to select a subset. But for the common case (OFX import →
book everything → reconcile all), one call instead of two, 300 fewer
tokens, zero risk of GUID copy errors.

Optional enhancement: `through_date` parameter that reconciles all
splits on or before a given date. Useful when the book has
post-statement transactions that shouldn't be included.

**Shipped:** v1.3 Stage 2 (PR #80, `feat/v1.3-stage-2`).

---

## `reconcile_account` does not accept account shortcuts

**Original problem statement:**
`reconcile_account` rejects `%xxxxxxx` account shortcuts in the
`account` parameter, requiring the full account path. Every other
tool that accepts an account name resolves shortcuts correctly. This
was flagged three times during the April–May 2026 production
session.

**Original plan:**
Wire the `_resolve_account_shortcut` helper into `reconcile_account`'s
account parameter handling, same as every other tool.

**Shipped:** Folded into the v1.3 short-GUID rollout (PR #53,
`feat/short-account-guids`) which threaded `_resolve_account`
support through every account-accepting tool, `reconcile_account`
included.

---

## `employee` owner_type validation on business tools

**Original problem statement:**
`unpost_invoice(id="000001", owner_type="employee")` silently
ignores the invalid owner_type, falls through to the unfiltered
lookup, and returns a confusing disambiguation error between
customer invoice and vendor bill. The LLM gets redirected to valid
options ("customer" or "vendor") but the error message doesn't
mention that "employee" was invalid input.

**Original plan:**
Validate `owner_type` against `["customer", "vendor"]` (or
`["customer", "vendor", "employee"]` once expense vouchers exist) at
the entry point of `unpost_invoice`, `post_invoice`, `pay_invoice`,
and any other tool that accepts it. Reject with:
`"Invalid owner_type 'employee'. Must be 'customer' or 'vendor'."`

**Shipped:** v1.3 Stage 3 vouchers (PR #86, `feat/vouchers`) made
`employee` a valid owner_type. The accompanying
`_gate_owner_type` helper in `tools/_helpers.py` enforces
validation at every business-tool entry point and rejects typos
with a clear message naming the valid options.

---

## Why archive instead of just deleting

These findings are kept because they represent a working pattern
that the project has come to rely on: bookkeeper review loop
generates real production signal, which becomes the dominant input
to the backlog. Preserving the original framing — even after the
work shipped — documents the loop's value so future contributors
can see what the signal actually looks like.
