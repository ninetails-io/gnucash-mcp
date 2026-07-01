# Batch Transaction Entry Specification

## GnuCash MCP Server — Post-v1.3.1 Feature

Status: **Draft** (design agreed; pending implementation)
Author: design session 2026-06-13
Supersedes nothing; extends the existing `create_transaction` write path.

---

## Executive Summary

A new tool, **`create_transactions`** (plural), lets a caller submit
**multiple transactions in one command** and receive a per-transaction
result it can correlate back to its input.

This is the largest change to the app's write workflow since the
ledger primitives were built. Today every transaction is a separate
`create_transaction` call — one book open, one save, one round-trip
each. Batch entry amortizes that: **one book open and one atomic save
for N transactions**, which is the v1.0.2 output-efficiency instinct
finally applied to the *write* path.

The hard problem batch entry introduces is not the writing — it is the
**response**: how to return N independent results (created / rejected /
duplicate-flagged), each carrying its own list of potential-duplicate
matches, in a shape the calling LLM can correlate to what it sent. This
spec fixes that shape.

---

## Background: Why This Matters

- **Per-call overhead, amortized.** A book open is the hot path
  (20–50 ms each, logged as "Book opened in X ms"). Entering a month of
  activity one transaction at a time pays that cost N times. Batch pays
  it once.
- **Bookkeeper ergonomics.** The live tester routinely enters many
  transactions in a sitting — a statement's worth, a month of receipts.
  "Here are 40 entries" in one call beats 40 calls, both in wall-clock
  and in the caller's context budget.
- **It continues the efficiency lineage, not breaks from it.** v1.0.2
  minified the *output*; short-GUIDs trimmed the *handles*; this trims
  the *write round-trips*. Same principle: every byte and every
  round-trip either helps the caller or is waste.

---

## Tool Surface

```
create_transactions(
    transactions: str,          # TSV block (header + N rows). Required.
    force: bool = False,        # batch-level duplicate override (see D7)
    dry_run: bool = False,      # validate + screen, write nothing
    on_error: str = "abort",    # "abort" | "skip"  (structural errors; see D6)
) -> dict                       # thin JSON envelope (see Output Format)
```

Module placement: `transactions` sub-module (alongside
`create_transaction`), `core` group. The book method
`create_transactions` lives on `CoreMixin`; the tool wrapper in
`tools/core.py` registers it and is added to
`TOOL_MODULES["transactions"]`.

---

## Input Format

A single TSV string: **one header row, then one row per transaction.**
Splits are encoded as repeated `(amount, account)` column pairs,
widening rightward as far as the widest transaction requires.

```
ref	date	description	amt1	acct1	amt2	acct2	amt3	acct3
1	2026-05-21	Capital One CC Payment	-500	Assets:Current Assets:Checking Account	500	Liabilities:Credit Card:Capital One
2	2026-05-21	Northgate 76 Gas	-54.19	Assets:Current Assets:Checking Account	54.19	Expenses:Auto:Fuel
```

### Columns

| Column        | Meaning                                                      |
|---------------|-------------------------------------------------------------|
| `ref`         | Caller-assigned correlation key (see D1). Required, unique within the batch. |
| `date`        | ISO `YYYY-MM-DD`.                                            |
| `description` | Transaction description.                                     |
| `amtN` `acctN`| Split amount + account, repeated. Amount is a string Decimal; account is a path, `%short`, or full GUID. |

### Parse contract

- First three fields are fixed: `ref`, `date`, `description`.
- **Everything after `description` is read in `(amount, account)`
  pairs to end of row** — the server is a pair-reader, not a
  fixed-column reader, so rows may be **ragged** (transaction 1 with
  two splits, transaction 2 with three). The header is sized to the
  widest row; narrower rows simply end earlier.
- Per-row validation (structural — see D6): even trailing-field count,
  **≥ 2 splits**, splits **balance to zero** in transaction currency,
  every account resolves, date parses.
- Batch validation: every `ref` is **non-empty and unique**; a
  collision fails the whole submission as a caller bug.

Splits are denormalized (wide) deliberately — see D2. Per-split memo,
per-transaction currency/notes: see Open Questions.

---

## Output Format

A **thin JSON envelope** whose values are **TSV strings, each with a
header row.** Never a single TSV blob with embedded section markers
(see D3).

```json
{
  "results": "ref\tstatus\ttxn_guid\tdup_count\treason\n1\tcreated\ta1f3\t0\t\n2\tcreated\t77bd\t2\t\n3\trejected\t\t1\tduplicate_detected",
  "duplicates": "ref\tconfidence\tguid\tdate\tamount\tdescription\tsignals\n3\tHIGH\t4b91\t2026-05-21\t-500\tCapital One CC Payment\tamt,desc,date"
}
```

### `results` table — always present, one row per input transaction

| Column     | Created row            | Rejected row                         |
|------------|------------------------|--------------------------------------|
| `ref`      | echoed verbatim        | echoed verbatim                      |
| `status`   | `created`              | `rejected`                           |
| `txn_guid` | short transaction GUID | blank                                |
| `dup_count`| advisory matches (≥0)  | matches that caused/accompanied it   |
| `reason`   | blank                  | machine code (e.g. `duplicate_detected`, or the structural validation message) |

- **`txn_guid` is the transaction handle, not split GUIDs** (D5).
  Callers wanting splits call `get_transaction(guid)`.
- **`reason` reuses single-entry's exact value** for duplicates —
  `duplicate_detected` — so a caller that learned single-entry reads a
  batch rejection identically. The "retry with force=true" hint is
  **not** repeated per row; it lives once in the tool docstring (D8).

### `duplicates` table — sparse; present only when matches exist

Reuses single-entry's `_duplicates_to_tsv` field order **with `ref`
prepended** as the foreign key:

```
ref   confidence   guid   date   amount   description   signals
```

- When no transaction has any potential-duplicate match, the field is
  emitted as `""` and dropped by `_strip_noise` — so **absence of the
  `duplicates` key means "no duplicates anywhere."**
- It carries both **blocking** matches (HIGH, which rejected a row) and
  **advisory** matches (MED, which merely annotated a created row).
  `status` + `reason` on the parent `results` row say which.

### `dup_count` is a pointer *and* a checksum

It points the caller from a `results` row into the `duplicates` table
by `ref`. It is also a self-check: **Σ `dup_count` over `results` must
equal the data-row count of `duplicates`.** A caller can verify it
parsed both tables consistently.

---

## Behavior

A **three-phase** model. The phases give a clean, defensible answer to
the two questions that haunt batch writes — atomicity, and what
"rejected" means.

### Phase 1 — Validate all (structural; atomic gate)

Parse and structurally validate every row via the existing
`_validate_transaction_splits` chokepoint (balance, account
resolution, currency, sign). **Structural failures are fatal to the
batch** under the default `on_error="abort"`: nothing is written, and
`results` reports each row — failed rows with their reason, valid rows
with `status=rejected`, `reason=batch_aborted`. Rationale in D6.

### Phase 2 — Duplicate screen (per-row)

All surviving rows are structurally valid. Run the existing
duplicate-detection signals per row. A **blocking** (HIGH) match with
`force=False` marks that row `rejected` / `duplicate_detected` and
**removes it from the write set** — but does **not** abort the batch.
Other rows proceed. **Duplicates are fatal only to the row.** MED
matches are advisory: the row stays in the write set and its
`dup_count` reflects the matches.

### Phase 3 — Commit the accepted set (one atomic save)

The write set = structurally-valid rows minus blocked duplicates. They
are built and committed in **one `book.save()`** — the per-write
overhead amortized. Each committed transaction gets its **own audit-log
entry** (see Open Questions on batch audit shape).

### The rule, in one sentence

> **Structural errors are fatal to the batch; duplicates are fatal only
> to the row.**

Because the two mean different things: a structural error means *the
request is malformed* (likely a systematic caller mistake — wrong
column mapping, off-by-one in the split pairs), so writing the "good"
rows risks landing subtly-wrong financial data; a duplicate means *the
request is valid* and you are merely flagging that an entry may already
exist, which the caller reviews per-row.

### `dry_run`

Runs Phases 1–2 and returns the full `results` + `duplicates`
envelope **without writing.** This is the clean "review before commit"
workflow: submit → see what would be created and what looks like a
duplicate → prune or force → resubmit. Mirrors single-entry's existing
`dry_run`.

---

## Design Decisions

### D1: Correlation via caller-supplied `ref` (ordinal-as-data)

Positional pairing (result[i] ↔ input[i]) is rejected: any reorder,
drop, or pre-validation skip shifts indices and silently misaligns
everything after the divergence. Instead the caller stamps each row
with a `ref` the server **echoes verbatim and never interprets**.

The `ref` is required (not optional-with-index-fallback — an optional
key means two code paths and the fragile one gets used), validated
unique-within-batch, and treated as opaque. **It may simply be the row
ordinal (`1..N`)** — "position carried as *data*, not as an
*invariant*." That gives positional simplicity with explicit
robustness: a server reorder or a dropped row can't cause the silent
cascade, because the pairing is written down in both tables.

This is *independently required* by the two-table output: the
`duplicates` table needs an explicit foreign key back to its parent
transaction regardless of atomicity, and once you need it there, using
the same key for top-level correlation is one mechanism instead of two.

### D2: Splits denormalized on input (wide), duplicates normalized on output (tall)

These look inconsistent but answer two different cardinality regimes:

- **Splits: known and bounded.** The *caller* knows the split count and
  it is small. Known-and-bounded → widen (repeat columns, one row).
- **Duplicates: discovered and unbounded.** The *server* finds them;
  the count is unknown ahead of time and varies per row. → separate
  table with an FK. You cannot widen what you cannot count.

The same test says when escaped-JSON-in-a-cell is appropriate (ragged,
*heterogeneous* nested data) versus a flat sub-table (*uniform* nested
data). Duplicate rows are uniform → flat table, not a JSON cell.

### D3: Two named TSV fields in a JSON envelope, not section markers in one TSV

The two tables are *heterogeneous* (different schemas). JSON is good at
a small set of named, differently-shaped fields; TSV is good at
homogeneous rows. Let the envelope do the outer split, TSV the inner
rows. Embedding `## RESULTS` / `## DUPLICATES` markers in one string
re-creates, by fragile textual convention, the structure the envelope
gives for free — and risks colliding with a transaction description
that contains the marker text. This is also **already the app's
convention**: single-entry returns a dict with
`result["duplicates"] = <tsv>`; batch is that pattern plus a `results`
field.

### D4: Headers on every TSV — reversing the house default

Historically TSV emitters omit the header and document the shape in the
docstring. That was a false economy the moment there was more than one
TSV shape to remember, and it is untenable when the caller must *join
two tables*. Batch emits headers on both tables. (Related: a small
app-wide pass to add headers to the existing `_*_to_tsv` emitters —
tracked separately, may ride this sprint.)

Interaction with D3's sparse handling: a header-only string is
non-empty, so it would defeat `_strip_noise`'s absence-as-signal.
Resolution: sparse tables emit **header+rows when there is data, `""`
when there is none** — never a lonely header.

### D5: Return the transaction handle, not split GUIDs

`create_transaction` today returns `{guid, status}` — the *transaction*
short-GUID, no split GUIDs. Batch matches it. Emitting split GUIDs would
introduce a *second* one-to-many (txn → splits) and drag the response
back toward the 3-D shape the two-table design exists to avoid. The
transaction handle is sufficient; `get_transaction(guid)` yields splits
on demand.

### D6: Structural errors abort the batch by default (`on_error="abort"`)

A malformed batch usually signals a *systematic* caller error (wrong
column mapping, off-by-one split pairs), and writing the rows that
happened to parse risks landing subtly-wrong financial data. Abort-all
forces a look before anything commits — consistent with the project's
"never write wrong data" posture and its existing validate-everything-
up-front-then-mutate pattern. `on_error="skip"` is offered as an opt-in
for callers who genuinely want skip-bad-keep-good.

### D7: Duplicate override — `force` semantics

Single-entry uses a per-call `force=true`. For batch, the v1 surface is
a **batch-level `force`** (override *all* blocking duplicates this
submission). A per-row `force` column is the more granular alternative
and is the recommended pairing with the `dry_run` → review → resubmit
flow, but it widens the input contract. See Open Questions.

### D8: The hint is not per-row

Single-entry pairs its duplicate rejection with a `hint` ("retry with
force=true"). Identical on every `duplicate_detected` row, so N copies
is waste. `reason` stays a terse machine code; the hint lives once in
the tool docstring.

---

## Consistency With Single-Entry (`create_transaction`)

| Concern             | Single-entry                          | Batch — same?                          |
|---------------------|---------------------------------------|----------------------------------------|
| Returned handle     | `{guid, status}` (txn short-GUID)     | ✅ `txn_guid` per row                   |
| Duplicate reject    | `reason="duplicate_detected"` + hint  | ✅ same `reason`; hint in docstring     |
| Duplicate render    | `_duplicates_to_tsv` (6 cols, no hdr) | ✅ same 6 cols + `ref` FK + header      |
| Override            | `force=True`                          | ✅ `force` (batch-level in v1)          |
| Dry run             | `dry_run`                             | ✅ `dry_run`                            |
| GUID prefixes       | short, `_unique_prefix`               | ✅ short                                |
| Validation          | `_validate_transaction_splits`        | ✅ reused per row                       |

The guiding principle: a caller that has learned single-entry should
read batch with *no new parsing rules* — only "there are N of them, and
they're keyed by `ref`."

---

## Testing Strategy

- **Unit (book method):** all-valid batch (atomic create, N audit
  entries); structural failure aborts under `abort` and skips under
  `skip`; blocking duplicate rejects its row but commits the rest;
  advisory (MED) duplicate creates with `dup_count>0`; `dry_run` writes
  nothing; ragged split widths; `ref` collision rejected; cross-batch
  duplicate (two identical rows in one submission — second flags the
  first).
- **Output contract:** `results` always present with header; `duplicates`
  absent when none, present-with-header when some; Σ `dup_count` ==
  `duplicates` row count (the checksum); every `ref` echoed verbatim.
- **Atomicity:** a Phase-3 failure (forced past a real problem) rolls
  back the whole save — no partial batch lands.
- **Persona integration:** a realistic month-of-activity batch against
  Alex (USD) and Lin Wei (CNY), including a multi-currency row, with
  cross-tool agreement (`get_book_summary` / `balance_sheet`) holding
  after the batch.
- **Regression:** single-entry `create_transaction` output unchanged.

---

## Implementation Plan

1. `CoreMixin.create_transactions(transactions, force, dry_run,
   on_error)` — parse TSV, Phase 1 (reuse `_validate_transaction_splits`
   per row), Phase 2 (reuse duplicate signals), Phase 3 (build all,
   one `book.save()`), assemble the envelope.
2. `_results_to_tsv(rows)` and a `ref`-prefixed variant of
   `_duplicates_to_tsv` — both with headers.
3. Tool wrapper in `tools/core.py`; add `create_transactions` to
   `TOOL_MODULES["transactions"]`.
4. Audit-log dispatch: decide batch shape (Open Questions) and add the
   formatter(s) to `logging_config.py`.
5. Tests per the strategy above.
6. Live bookkeeper validation on Alex + Lin Wei before any PR.

---

## Decisions (resolved 2026-06-13)

1. **`on_error` default = `abort`.** A structurally-malformed batch
   aborts entirely; nothing is written. Financial-data caution wins.
   `skip` (skip-bad-keep-good) remains an opt-in argument value.
2. **`force` = batch-level flag (v1).** A single `force=True` overrides
   all blocking duplicates this submission, matching single-entry's
   per-call force. A per-row force column is deferred.
3. **Per-split memo and per-transaction currency = omitted in v1.** The
   input stays `(amt, acct)` pairs only; memo-bearing or
   unusual-currency transactions use single-entry. Documented
   limitation, not a gap to apologize for.
4. **Audit-log shape = N individual entries.** Each committed
   transaction logs identically to a single-entry create — no new audit
   shape for the dispatcher or a human reader to learn.

## Open Questions (remaining — minor, may default during implementation)

5. **Batch size cap.** The app caps listings at 250. A submission cap
   (and its error message) needs a number — proposed default **100**;
   confirm during implementation.
6. **Codifying validation reasons.** Structural `reason` values are
   currently the validation message text. Whether to normalize them to
   short codes (like `duplicate_detected`) is a smaller follow-on.

---

## v1 Limitations (the wide format trades expressiveness for throughput)

Reading the real `create_transaction` confirmed the wide TSV implies a
deliberately narrow v1. Each case below falls back to single-entry
`create_transaction`:

- **Same-currency only** (book default). The `(amt, acct)` format has
  no `quantity` slot, so cross-currency / cross-commodity (investment)
  splits aren't expressible. Single-currency splits need no quantity
  (`value == quantity`), so this is clean, not a workaround.
- **No per-split memo, no per-transaction notes.**
- **No auto-fill** — every row supplies explicit splits.
- **No intra-batch duplicate detection.** Duplicates are detected
  against *existing* book data via `_collect_create_signals`. Two
  identical new rows in one batch both commit: the "don't flush
  mid-build" rule means an unsaved sibling has no GUID, and the
  DUPLICATES `guid` column assumes a persisted match. Deferred to v2.
- **No per-row advisory warnings** (e.g. split-consistency). RESULTS is
  status-focused; single-entry surfaces these.

## Out of Scope (this feature)

- Batch *update* / *delete* / *void* — this spec is create-only.
- Batch scheduled-transaction instantiation.
- Streaming / chunked responses for very large batches (the size cap
  in Open Q5 bounds this instead).
- Cross-transaction balancing (each row balances independently; the
  batch is not one super-transaction).
