# Bookkeeper validation spec — `feat/release-prep` branch

Branch: `feat/release-prep` (13 commits since `develop`).
Goal: confirm everything that landed still does what the
dashboard, invoices, and audit log were doing before — plus
exercise the four newly-added or newly-strict behaviors.

Run this against a working book that has at least some
cross-currency activity (otherwise the FX probes are no-ops).
Cowork's synthesized 6-month book is ideal.

Stop testing and flag immediately if **any** of the following:
- A `get_book_summary` total doesn't match `balance_sheet` /
  `net_worth` on the same date.
- A tool raises an unexpected exception (not the deliberate
  validation errors documented below).
- An audit log line shows a raw `%xxxxxxx` short GUID where it
  used to show a canonical path.

---

## Section 1 — Smoke (3 probes, < 1 min)

### 1.1 `get_book_summary`

Call it. The output should look exactly like it did before:
Book / Currency / Data range / Last entry, Warnings (if any),
Accounts / Assets / Liabilities sections, Receivables / Payables
breakouts, Reconciliation, Net worth trajectory, Monthly net,
Runway, Budget (if any), Transactions, Scheduled, Business,
Commodities.

**What changed underneath:** R-1 broke the 486-line method
into a dataclass + seven section renderers. Output should be
byte-identical to pre-branch. If any section is missing,
misordered, has wrong totals, or renders weirdly, that's R-1
broken.

### 1.2 Cross-tool agreement

Pick today's date. Call:
- `get_book_summary`
- `balance_sheet`
- `net_worth(end_date=today)`

The headline net-worth number should match across all three
(it's the same identity computed three ways). If they
disagree, something math-y regressed.

### 1.3 `get_server_config`

Call it once. Should return modules, tool count, book path,
debug mode, version. No surprises.

**What changed:** MP-1 documented why this tool deliberately
lacks `@audit_log` (LLMs call it reflexively for orientation;
logging every call would clutter the audit trail). No behavior
change.

---

## Section 2 — Invoices, payments, FX (R-2, the most important
section)

This is where R-2 lives — the cross-currency rate-lookup
shared between `post_invoice` and `pay_invoice` was consolidated
into one chokepoint. Both paths must still produce the same
math they did before.

### 2.1 Same-currency invoice round-trip

Pick a customer and post a small same-currency invoice ($X),
then pay it. Confirm:
- A/R debit then credit clean
- Lot opens and closes
- No FX-gain-loss split appears (none needed; same currency)

### 2.2 Cross-currency invoice posting

Pick a customer with a non-default currency (Berlin Digital in
EUR if you're on Alex, the CNY customers if Lin Wei, or any
foreign-currency customer in Cowork's book).

Post an invoice. Confirm:
- Posts cleanly
- The A/R account's quantity is in INVOICE currency (EUR)
- The transaction balances in invoice currency
- If the A/R account is in invoice currency, no rate needed; if
  A/R is in book default (USD) and invoice is foreign (EUR),
  the rate gets pulled from `book.prices`

If no rate is on file for the date, the error should read:
`"Cross-currency posting requires an exchange rate: invoice
currency EUR differs from target commodity USD, and no
matching price was found in the book for EUR/USD on or near
2026-XX-XX. Add a price with create_price, then retry."`

### 2.3 Cross-currency payment

Pay the invoice from 2.2 (use Checking — book default
currency).
- Realized FX gain/loss split should appear on the date the
  rate moved between post-date and payment-date
- Bank-side quantity in USD (bank's commodity), invoice-side
  quantity in EUR (A/R's commodity, if A/R is EUR)
- Lot closes

### 2.4 Payment failure mode — extreme rate

Try paying a cross-currency invoice with a payment amount
small enough that `amount × rate` quantizes to zero in the
target commodity. Should refuse with a clear
"quantizes to zero" error. (Skip this if you don't have a
convenient fixture.)

---

## Section 3 — Audit log canonical paths (R-3)

R-3 moved the audit-log's account-ref-to-fullname rewrite into
the book layer. The output should still show canonical paths,
not `%shortguid`.

### 3.1 Write something, read the audit log

Do a small `set_reconcile_state` or `update_transaction` using
a `%short` account reference. Then call `get_audit_log` for
today.

The rendered audit line should show the canonical
`Assets:Checking Account` (or whatever), **not**
`%abc1234`. If you see a raw `%shortguid` where a path used to
appear, that's R-3 broken.

### 3.2 Splits-in-params normalization

`replace_splits` or `create_transaction` with `%short`
account refs in the splits list. Same expectation: the audit
log shows canonical paths in the splits breakdown.

---

## Section 4 — `create_budget` with `start_date` (NEW feature)

This is the only **new user-visible feature** on this branch.

### 4.1 Retroactive budget

Create a budget that starts BEFORE today:

```
create_budget(
    name="2024 Retroactive Test",
    start_date="2024-01-01",
    num_periods=12,
    period_type="monthly"
)
```

Should succeed. Response includes `"start_date": "2024-01-01"`.

Then call `get_budget_report(budget_name="2024 Retroactive
Test", period="all")` — should show all 12 periods of 2024
with whatever actuals the book has for those months. The
"compare freshly-authored target to historical actuals" use
case the bookkeeper flagged after PR #98.

### 4.2 Mid-year start

```
create_budget(
    name="Mid-Year Test",
    start_date="2025-07-01",
    num_periods=6,
    period_type="monthly"
)
```

Should anchor the first period to July 1, 2025. Confirm via
`get_budget`.

### 4.3 Conflict resolution

When both `year` and `start_date` are passed, `start_date`
wins (it's the more specific signal). Try:

```
create_budget(
    name="Both Args Test",
    year=2026,
    start_date="2025-07-01",
    num_periods=6
)
```

Response should show `start_date: 2025-07-01`. `year=2026` is
silently ignored.

### 4.4 Malformed start_date

```
create_budget(name="Bad", start_date="2025/01/01")
```

Should raise: `"Invalid start_date '2025/01/01': must be
YYYY-MM-DD ISO format. ..."`

### 4.5 Backward compatibility

Old call style (no `start_date`) must still work:

```
create_budget(name="Legacy Style", year=2026, num_periods=12)
```

Should anchor to `2026-01-01` exactly like before.

---

## Section 5 — `debt_payoff_plan` error messages (MP-9)

The error message for "no debt accounts" was split into two
branches so the next action is clear. Try:

### 5.1 Book with debt accounts but no APR slot

If you have any CREDIT or LIABILITY accounts without
`apr` slots set, calling `debt_payoff_plan(monthly_budget="500")`
should raise:

```
Found N CREDIT/LIABILITY account(s) but none have an 'apr'
slot set (or every APR is <= 0, or every balance is <= 0).
Use set_account_slot to set 'apr' on the debt accounts you
want included in the payoff plan.
```

The N should be the actual count.

### 5.2 Verify it still works on a book with APR set

If you have at least one CREDIT/LIABILITY account WITH a valid
`apr` slot AND a positive balance, `debt_payoff_plan` should
work as it always has — same numbers as before R-arc.

---

## Section 6 — Input gates (MP-5, MP-13, MP-14)

These are defense-in-depth: stricter rejection of bad inputs
at the entry point.

### 6.1 Create account with `:` in name (MP-14)

```
create_account(name="Bad:Name", account_type="ASSET", parent="Assets")
```

Should refuse:
`"Account name cannot contain ':' (path separator). Got:
'Bad:Name'. To create a nested account, pass the leaf name
as ``name`` and the full parent path as ``parent``."`

### 6.2 Create account with empty name (MP-14)

```
create_account(name="", account_type="ASSET", parent="Assets")
create_account(name="   ", account_type="ASSET", parent="Assets")
```

Both should refuse with "Account name cannot be empty".

### 6.3 Create commodity with empty fields (MP-13)

```
create_commodity(mnemonic="", fullname="Test", namespace="FUND")
create_commodity(mnemonic="X", fullname="", namespace="FUND")
create_commodity(mnemonic="X", fullname="Test", namespace="FUND", fraction=0)
create_commodity(mnemonic="X", fullname="Test", namespace="FUND", fraction=-1)
```

All should raise with clear messages naming the bad field.

### 6.4 Business entity free-text caps (MP-5)

```
create_customer(name="Test", notes="X" * 5000)
```

Should refuse with "notes exceeds 4096-byte cap ...".

```
create_customer(name="Test", address={"addr1": "X" * 2000})
```

Should refuse with "address.addr1 exceeds 1024-byte cap ...".

---

## Section 7 — Backup tool guards (MP-3) + path redaction (MP-4)

### 7.1 Auto-stage footgun guard (MP-3)

WITHOUT setting any `stage`, try:

```
prune_backups(keep_last_n=0)
```

Should refuse:
`"Refusing to delete every auto backup at once. ..."`

This is a NEW guard. Pre-branch it would have silently wiped
every session/weekly/monthly backup.

### 7.2 Explicit auto-stage works (still allowed)

```
prune_backups(keep_last_n=0, stage="session")
```

This SHOULD work — the user opted into wiping that specific
stage. Confirm session backups are deleted but weekly/monthly
survive.

### 7.3 Path redaction (MP-4)

Set the env var `GNUCASH_REDACT_PATHS=1` and restart the
server. Then:

```
create_backup(stage="manual", label="redaction-test")
```

Response should show:
- `path: <basename only, no slashes>`
- `restore_hint: ... mv <basename> <basename>.broken && cp <basename> <basename>`

(filenames preserved; directory paths gone)

Then `list_backups()` — every entry's `path` field should
also be basename-only.

UNSET the env var, restart. Same calls should now show full
absolute paths (default behavior). Confirm the env var
correctly opts in and out.

---

## Section 8 — Existing-feature regression (quick spot-checks)

### 8.1 Reconciliation backlog

`get_book_summary` should still show the reconciliation
section. Stale accounts should still get the `⚠` marker and
the new `(N years behind, oldest: YYYY-MM-DD)` wording from
the v1.3 HP-8 work — same as before this branch.

### 8.2 `cash_flow` transfer filter

`cash_flow(start_date, end_date)` should still filter internal
transfers by default and surface `transfers_excluded` count
when relevant. `include_transfers=True` should still produce
the gross flow.

### 8.3 Voided transactions

If you have any voided transactions, `cash_flow` should still
treat them symmetrically (zombies, contribute nothing) and
`get_unreconciled_splits` / `get_book_summary` should still
exclude them from the unreconciled count.

### 8.4 FX gain/loss recognition

A cross-currency invoice payment where the rate moved between
post-date and pay-date should still produce a realized
FX-gain/loss split. (R-2 should not have changed this — it
was meant to consolidate the rate lookup, not the gain/loss
math.)

---

## Reporting back

For each section, one of:
- **PASS** — behavior matches expectations.
- **FAIL** — observed something different. Include the exact
  call, the unexpected output, and the section + sub-section
  number.

Section 1 and Section 2 are the highest-priority. R-1 touches
the most lines; R-2 touches the most numbers. Sections 4 and
6.x are the user-facing changes most likely to surprise.

If you exhaust the spec and everything's clean, the branch is
ready for the PR. If anything failed, leave it open and report
back so Claude can investigate before the PR opens.
